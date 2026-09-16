from __future__ import annotations

import http.client
import importlib.util
import io
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "seo_product_gateway", ROOT / "runtime" / "product" / "gateway.py"
)
assert SPEC is not None and SPEC.loader is not None
GATEWAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATEWAY)


class StubUpstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ProductGatewayTest(unittest.TestCase):
    def _gateway(self, rate_limit_per_minute: int) -> tuple[ThreadingHTTPServer, threading.Thread, int]:
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), StubUpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        self.addCleanup(upstream.shutdown); self.addCleanup(upstream.server_close)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            GATEWAY.handler(
                "product",
                f"http://127.0.0.1:{upstream.server_port}",
                rate_limit_per_minute=rate_limit_per_minute,
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.shutdown); self.addCleanup(server.server_close)
        return server, thread, server.server_port

    def _get(self, port: int, path: str, headers: dict[str, str] | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheader("Retry-After"), response.read()

    def test_rate_limit_returns_429_with_retry_after(self) -> None:
        _, _, port = self._gateway(rate_limit_per_minute=2)
        self.assertEqual(self._get(port, "/health")[0], 200)
        self.assertEqual(self._get(port, "/health")[0], 200)
        status, retry_after, body = self._get(port, "/health")
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(body)["code"], "rate_limited")
        self.assertTrue(retry_after is not None and int(retry_after) >= 1)

    def test_rate_limit_counts_rejected_routes_too(self) -> None:
        _, _, port = self._gateway(rate_limit_per_minute=1)
        self.assertEqual(self._get(port, "/no-such-route")[0], 404)
        self.assertEqual(self._get(port, "/health")[0], 429)

    def test_parse_rate_limit_defaults_and_rejects_garbage(self) -> None:
        self.assertEqual(GATEWAY.parse_rate_limit(None), GATEWAY.DEFAULT_RATE_LIMIT_PER_MINUTE)
        self.assertEqual(GATEWAY.parse_rate_limit(""), GATEWAY.DEFAULT_RATE_LIMIT_PER_MINUTE)
        self.assertEqual(GATEWAY.parse_rate_limit("30"), 30)
        for bad in ("0", "-5", "lots", "12.5"):
            with self.assertRaises(ValueError, msg=bad):
                GATEWAY.parse_rate_limit(bad)

    def test_access_log_omits_authorization_value(self) -> None:
        _, _, port = self._gateway(rate_limit_per_minute=60)
        secret = "gw-s3cr3t-value"
        stream = io.StringIO()
        lines: list[str] = []
        with mock.patch.object(GATEWAY.sys, "stderr", stream):
            self.assertEqual(
                self._get(port, "/health", headers={"Authorization": f"Bearer {secret}"})[0], 200
            )
            for _ in range(300):
                lines = [line for line in stream.getvalue().splitlines() if line.strip()]
                if lines:
                    break
                time.sleep(0.01)
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["method"], "GET")
        self.assertEqual(record["route"], "/health")
        self.assertEqual(record["status"], 200)
        self.assertNotIn(secret, stream.getvalue())


if __name__ == "__main__":
    unittest.main()
