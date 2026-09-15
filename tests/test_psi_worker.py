from __future__ import annotations

import importlib.util
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("seo_psi_worker", ROOT / "runtime" / "psi" / "entrypoint.py")
assert SPEC is not None and SPEC.loader is not None
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)


PSI_RESPONSE = {
    "lighthouseResult": {
        "audits": {
            "largest-contentful-paint": {"numericValue": 5123.0},
            "interaction-to-next-paint": {"numericValue": 180.0},
            "cumulative-layout-shift": {"numericValue": 0.02},
        }
    },
    "loadingExperience": {
        "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 3400},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 210},
            "CUMULATIVE_LAYOUT_SHIFT": {"percentile": 5},
        }
    },
}

CRUX_RESPONSE = {
    "record": {
        "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentiles": {"p75": "3210"}},
            "INTERACTION_TO_NEXT_PAINT": {"percentiles": {"p75": "190"}},
            "CUMULATIVE_LAYOUT_SHIFT": {"percentiles": {"p75": "0.01"}},
        }
    }
}


class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/ok":
            body = b"hello"
        elif self.path == "/big":
            body = b"x" * 100
        elif self.path == "/redir-same":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/redir-foreign":
            self.send_response(302)
            self.send_header("Location", "http://other.test/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class PsiWorkerTest(unittest.TestCase):
    def _stub(self) -> tuple[ThreadingHTTPServer, int]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server, server.server_port

    def test_extractors_read_google_shapes(self) -> None:
        self.assertEqual(
            WORKER.extract_lab_metrics(PSI_RESPONSE),
            {"lcp": 5123.0, "cls": 0.02, "inp": 180.0},
        )
        self.assertEqual(
            WORKER.extract_loading_experience(PSI_RESPONSE),
            {"lcp": 3400.0, "cls": 5.0, "inp": 210.0},
        )
        self.assertEqual(
            WORKER.extract_crux_origin(CRUX_RESPONSE),
            {"lcp": 3210.0, "cls": 0.01, "inp": 190.0},
        )
        self.assertEqual(
            WORKER.extract_lab_metrics({"lighthouseResult": {}}),
            {"lcp": None, "cls": None, "inp": None},
        )
        self.assertEqual(
            WORKER.extract_lab_metrics(None), {"lcp": None, "cls": None, "inp": None}
        )

    def test_sample_urls_caps_and_filters_origins(self) -> None:
        sitemap = (
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc>https://example.com/a</loc></url>"
            b"<url><loc>https://other.test/b</loc></url>"
            b"<url><loc>https://example.com/c</loc></url>"
            b"<url><loc>https://example.com/d</loc></url>"
            b"</urlset>"
        )
        self.assertEqual(
            WORKER.sample_urls("https://example.com/", sitemap, 3),
            ["https://example.com/", "https://example.com/a", "https://example.com/c"],
        )
        self.assertEqual(
            WORKER.sample_urls("https://example.com/", b"not xml", 3),
            ["https://example.com/"],
        )

    def test_fetch_rejects_non_allowlisted_private_and_credential_urls(self) -> None:
        _, port = self._stub()
        base = f"http://127.0.0.1:{port}"
        with self.assertRaises(ValueError):
            WORKER.fetch_bytes(f"{base}/ok", 5.0, 100, allowed_hosts={"example.com"})
        with self.assertRaises(ValueError):
            WORKER.fetch_bytes(f"{base}/ok", 5.0, 100, allowed_hosts={"127.0.0.1"})
        with self.assertRaises(ValueError):
            WORKER.fetch_bytes(
                f"http://user:pass@127.0.0.1:{port}/ok", 5.0, 100, allowed_hosts={"127.0.0.1"}
            )

    def test_fetch_follows_same_origin_redirects_and_caps_bytes(self) -> None:
        _, port = self._stub()
        base = f"http://127.0.0.1:{port}"
        with mock.patch.dict("os.environ", {"PSI_ALLOW_PRIVATE": "1"}):
            status, body, truncated = WORKER.fetch_bytes(
                f"{base}/redir-same", 5.0, 100, allowed_hosts={"127.0.0.1"}
            )
            self.assertEqual((status, body, truncated), (200, b"hello", False))
            status, body, truncated = WORKER.fetch_bytes(
                f"{base}/big", 5.0, 10, allowed_hosts={"127.0.0.1"}
            )
            self.assertEqual((status, truncated), (200, True))
            self.assertEqual(body, b"x" * 10)
            status, _, _ = WORKER.fetch_bytes(
                f"{base}/missing", 5.0, 100, allowed_hosts={"127.0.0.1"}
            )
            self.assertEqual(status, 404)
            with self.assertRaises(ValueError):
                WORKER.fetch_bytes(
                    f"{base}/redir-foreign", 5.0, 100, allowed_hosts={"127.0.0.1"}
                )

    def test_site_fetch_pins_the_validated_public_address(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.getheader.return_value = None
        response.read.return_value = b"ok"
        connection = mock.MagicMock()
        connection.getresponse.return_value = response
        with (
            mock.patch.object(WORKER, "_global_addresses", return_value=["93.184.216.34"]),
            mock.patch.object(WORKER, "_PinnedHTTPConnection", return_value=connection) as factory,
        ):
            result = WORKER.fetch_bytes(
                "http://example.com/path?q=1", 5, 10, allowed_hosts={"example.com"}
            )
        factory.assert_called_once_with("example.com", 80, "93.184.216.34", mock.ANY)
        connection.request.assert_called_once_with(
            "GET", "/path?q=1", headers={"Host": "example.com", "User-Agent": "ExtellaPSI/2.1"}
        )
        self.assertEqual(result, (200, b"ok", False))

    def test_ipv6_host_header_keeps_uri_authority_brackets(self) -> None:
        address = "2001:4860:4860::8888"
        response = mock.MagicMock()
        response.status = 200
        response.getheader.return_value = None
        response.read.return_value = b"ok"
        for url, expected in (
            (f"http://[{address}]/", f"[{address}]"),
            (f"http://[{address}]:8080/", f"[{address}]:8080"),
        ):
            connection = mock.MagicMock()
            connection.getresponse.return_value = response
            with (
                self.subTest(url=url),
                mock.patch.object(WORKER, "_global_addresses", return_value=[address]),
                mock.patch.object(WORKER, "_PinnedHTTPConnection", return_value=connection),
            ):
                WORKER.fetch_bytes(url, 5, 10, allowed_hosts={address})
            self.assertEqual(connection.request.call_args.kwargs["headers"]["Host"], expected)

    def test_first_public_address_failure_falls_back_without_reresolving(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.getheader.return_value = None
        response.read.return_value = b"ok"
        working = mock.MagicMock()
        working.getresponse.return_value = response
        addresses = ["93.184.216.34", "1.1.1.1"]
        with (
            mock.patch.object(WORKER, "_global_addresses", return_value=addresses) as resolver,
            mock.patch.object(WORKER, "_PinnedHTTPConnection", side_effect=[OSError("down"), working]) as factory,
        ):
            result = WORKER.fetch_bytes("http://example.com/", 5, 10, allowed_hosts={"example.com"})
        self.assertEqual(result, (200, b"ok", False))
        self.assertEqual([call.args[2] for call in factory.call_args_list], addresses)
        resolver.assert_called_once()

    def test_response_failure_does_not_fall_back_to_another_address(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.read.side_effect = OSError("body down")
        first = mock.MagicMock()
        first.getresponse.return_value = response
        second = mock.MagicMock()
        with (
            mock.patch.object(WORKER, "_global_addresses", return_value=["93.184.216.34", "1.1.1.1"]),
            mock.patch.object(WORKER, "_PinnedHTTPConnection", side_effect=[first, second]) as factory,
        ):
            with self.assertRaises(OSError):
                WORKER.fetch_bytes("http://example.com/", 5, 10, allowed_hosts={"example.com"})
        self.assertEqual(factory.call_count, 1)

    def test_nat64_address_with_non_global_embedded_ipv4_is_rejected(self) -> None:
        answers = [
            (WORKER.socket.AF_INET6, WORKER.socket.SOCK_STREAM, 6, "", ("64:ff9b::a9fe:a9fe", 443, 0, 0)),
        ]
        with mock.patch.object(WORKER.socket, "getaddrinfo", return_value=answers):
            with self.assertRaisesRegex(ValueError, "not a global address"):
                WORKER._global_addresses("nat64.invalid", 443)

    def test_mixed_public_private_dns_answer_is_rejected(self) -> None:
        answers = [
            (WORKER.socket.AF_INET, WORKER.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (WORKER.socket.AF_INET, WORKER.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with mock.patch.object(WORKER.socket, "getaddrinfo", return_value=answers):
            with self.assertRaisesRegex(ValueError, "not a global address"):
                WORKER._global_addresses("example.com", 443)

    def test_api_json_call_pins_the_google_host(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"ok": true}'
        connection = mock.MagicMock()
        connection.getresponse.return_value = response
        with (
            mock.patch.object(WORKER, "_global_addresses", return_value=["142.250.1.1"]) as resolver,
            mock.patch.object(WORKER, "_PinnedHTTPSConnection", return_value=connection) as factory,
        ):
            value = WORKER._get_json("https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=x", 5)
        self.assertEqual(value, {"ok": True})
        factory.assert_called_once_with("www.googleapis.com", 443, "142.250.1.1", mock.ANY)
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Host"], "www.googleapis.com")
        resolver.assert_called_once()

    def test_api_json_call_rejects_a_non_google_host(self) -> None:
        with self.assertRaises(ValueError):
            WORKER._get_json("https://evil.invalid/steal", 5)

    def test_real_socket_path_uses_pinned_numeric_address_and_host(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        request = []

        def serve() -> None:
            raw, _peer = listener.accept()
            with raw:
                request.append(raw.recv(4096))
                raw.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            listener.close()

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            with (
                mock.patch.dict("os.environ", {"PSI_ALLOW_PRIVATE": "1"}),
                mock.patch.object(WORKER, "_global_addresses", return_value=["127.0.0.1"]),
            ):
                result = WORKER.fetch_bytes(
                    f"http://audit.example:{port}/path", 3, 10, allowed_hosts={"audit.example"}
                )
        finally:
            thread.join(3)
            listener.close()
        self.assertEqual(result, (200, b"ok", False))
        self.assertIn(b"Host: audit.example:" + str(port).encode(), request[0])

    def test_absolute_deadline_rejects_slow_request_response_and_body(self) -> None:
        clock = [0.0]

        class SlowResponse:
            status = 200
            length = None

            def getheader(self, _name: str, _default: object = None) -> None:
                return None

            def read(self, _limit: int) -> bytes:
                clock[0] += 0.45
                return b"ok"

        class SlowConnection:
            sock = None

            def request(self, *_args: object, **_kwargs: object) -> None:
                clock[0] += 0.45

            def getresponse(self) -> SlowResponse:
                clock[0] += 0.45
                return SlowResponse()

            def close(self) -> None:
                return None

        with (
            mock.patch.object(WORKER.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(WORKER, "_global_addresses", return_value=["93.184.216.34"]),
            mock.patch.object(WORKER, "_PinnedHTTPConnection", return_value=SlowConnection()),
        ):
            with self.assertRaises(TimeoutError):
                WORKER.fetch_bytes("http://example.com/", 1, 10, allowed_hosts={"example.com"})
        self.assertGreater(clock[0], 1.0)

    def test_dns_stage_cannot_complete_after_the_absolute_deadline(self) -> None:
        clock = [0.0]

        def slow_resolve(*_args: object, **_kwargs: object):
            clock[0] += 1.1
            return [(WORKER.socket.AF_INET, WORKER.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

        with (
            mock.patch.object(WORKER.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(WORKER.socket, "getaddrinfo", side_effect=slow_resolve),
        ):
            with self.assertRaises(TimeoutError):
                WORKER.fetch_bytes("http://example.com/", 1, 10, allowed_hosts={"example.com"})

    def test_run_probe_merges_lab_field_and_sitefiles(self) -> None:
        def fake_fetch(url: str, timeout: float, max_bytes: int, **_kwargs: object):
            if url.endswith("/robots.txt"):
                return 200, b"User-agent: *\nDisallow:\n", False
            if url.endswith("/sitemap.xml"):
                return 404, b"", False
            return 200, b"<html><head></head></html>", False

        with (
            mock.patch.object(WORKER, "fetch_bytes", side_effect=fake_fetch),
            mock.patch.object(WORKER, "_get_json", return_value=PSI_RESPONSE),
            mock.patch.object(WORKER, "_post_json", return_value=CRUX_RESPONSE),
        ):
            payload = WORKER.run_probe("https://example.com/", 3, 60000, "test-key")
        self.assertEqual(payload["schema"], "extella.psi_source.v1")
        self.assertEqual(payload["probed_urls"], ["https://example.com/"])
        entries = {(item["metric"], item["url"]) for item in payload["metrics"]}
        self.assertEqual(
            entries,
            {("lcp", "https://example.com/"), ("cls", "https://example.com/"), ("inp", "https://example.com/")},
        )
        lcp = next(item for item in payload["metrics"] if item["metric"] == "lcp")
        self.assertEqual(lcp["lab"], 5123.0)
        self.assertEqual(lcp["field_p75"], 3210.0)
        self.assertEqual(payload["sitefiles"]["robots_txt"]["http_status"], 200)
        self.assertNotIn("test-key", json.dumps(payload))

    def test_run_probe_without_key_skips_crux(self) -> None:
        with (
            mock.patch.object(WORKER, "fetch_bytes", return_value=(404, b"", False)),
            mock.patch.object(WORKER, "_get_json", return_value=PSI_RESPONSE),
            mock.patch.object(WORKER, "_post_json") as post,
        ):
            payload = WORKER.run_probe("https://example.com/", 1, 60000, "")
        post.assert_not_called()
        lcp = next(item for item in payload["metrics"] if item["metric"] == "lcp")
        self.assertEqual(lcp["field_p75"], 3400.0)

    def test_run_probe_total_outage_is_declared_unavailable(self) -> None:
        with (
            mock.patch.object(WORKER, "fetch_bytes", return_value=(0, b"", False)),
            mock.patch.object(WORKER, "_get_json", side_effect=OSError("down")),
        ):
            payload = WORKER.run_probe("https://example.com/", 1, 60000, "")
        self.assertEqual(payload, {"status": "unavailable", "reason": "http_503"})

    def test_run_probe_reports_degraded_psi_api_without_key_or_url(self) -> None:
        import io
        import urllib.error

        def fail(url: str, timeout: float) -> object:
            raise urllib.error.HTTPError(url, 429, "quota", {}, None)

        stream = io.StringIO()
        with (
            mock.patch.object(WORKER, "fetch_bytes", return_value=(200, b"<html></html>", False)),
            mock.patch.object(WORKER, "_get_json", side_effect=fail),
            mock.patch.object(WORKER, "_post_json", side_effect=OSError("down")),
            mock.patch.object(WORKER.sys, "stderr", stream),
        ):
            payload = WORKER.run_probe("https://example.com/", 1, 60000, "secret-key")
        self.assertEqual(payload["psi_api"], {"status": "degraded", "reason": "http_429"})
        self.assertEqual(payload["crux"]["status"], "unavailable")
        self.assertNotIn("secret-key", stream.getvalue())
        self.assertNotIn("example.com", stream.getvalue())
        self.assertIn("psi psi error", stream.getvalue())

    def test_run_probe_detects_error_json_payloads(self) -> None:
        with (
            mock.patch.object(WORKER, "fetch_bytes", return_value=(200, b"<html></html>", False)),
            mock.patch.object(WORKER, "_get_json", return_value={"error": {"code": 403}}),
            mock.patch.object(WORKER, "_post_json", return_value={"error": {"code": 503}}),
        ):
            payload = WORKER.run_probe("https://example.com/", 1, 60000, "k")
        self.assertEqual(payload["psi_api"], {"status": "degraded", "reason": "http_403"})
        self.assertEqual(payload["crux"], {"status": "unavailable", "reason": "http_503"})

    def test_handle_run_validates_strictly(self) -> None:
        status, _ = WORKER.handle_run(b"not json")
        self.assertEqual(status, 400)
        status, _ = WORKER.handle_run(json.dumps({"site_url": "https://example.com/"}).encode())
        self.assertEqual(status, 400)
        bad_plan = {"site_url": "https://example.com/", "plan": {"max_urls": 9, "timeout_ms": 1000}}
        status, _ = WORKER.handle_run(json.dumps(bad_plan).encode())
        self.assertEqual(status, 400)
        with mock.patch.object(WORKER, "run_probe", return_value={"status": "ok"}) as probed:
            ok_plan = {"site_url": "https://example.com/", "plan": {"max_urls": 2, "timeout_ms": 1000}}
            status, payload = WORKER.handle_run(json.dumps(ok_plan).encode())
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})
        self.assertEqual(probed.call_args[0][:3], ("https://example.com/", 2, 1000))


if __name__ == "__main__":
    unittest.main()
