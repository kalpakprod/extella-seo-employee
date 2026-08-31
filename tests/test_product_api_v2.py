from __future__ import annotations

import importlib.util
import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "experts"))
SERVER_PATH = ROOT / "runtime" / "product" / "server.py"
SPEC = importlib.util.spec_from_file_location("seo_product_server_v2", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class ProductApiV2Test(unittest.TestCase):
    def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> int:
        server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.handler("x" * 32))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse(); response.read(); return response.status
        finally:
            server.shutdown(); server.server_close(); thread.join(1)

    def test_exact_request_allowlists_and_queue_status(self) -> None:
        with mock.patch.object(SERVER, "seo_employee_run", return_value=json.dumps({"status": "success", "method": "run", "state": "queued", "queue_item": {}, "duplicate": False})) as call:
            status, _ = SERVER.dispatch("POST", "/api/run", {"target_id": "target-example-com-0f115db0"})
        self.assertEqual(status, 202)
        self.assertEqual(call.call_args.kwargs, {"method": "run", "target_id": "target-example-com-0f115db0", "mode": "", "trigger": "manual"})
        self.assertEqual(SERVER.dispatch("POST", "/api/run", {"target_id": "target-example-com-0f115db0", "prompt": "ignored"})[0], 400)
        self.assertEqual(SERVER.dispatch("POST", "/api/configure", {"site_url": "https://example.com/", "unknown": True})[0], 400)

    def test_state_requires_target_unless_exactly_one_is_configured(self) -> None:
        with mock.patch.object(SERVER, "seo_employee_state", return_value=json.dumps({"state": "empty"})) as state:
            status, payload = SERVER.dispatch("GET", "/api/state?target_id=target-example-com-0f115db0", {})
        self.assertEqual((status, payload), (200, {"state": "empty"}))
        self.assertEqual(state.call_args.kwargs, {"target_id": "target-example-com-0f115db0"})
        self.assertEqual(SERVER.dispatch("GET", "/api/state?unexpected=1", {})[0], 400)

    def test_state_without_target_is_rejected_for_multiple_configured_targets(self) -> None:
        with mock.patch.object(SERVER, "list_target_states", return_value={"status": "success", "targets": [{}, {}]}):
            self.assertEqual(SERVER.dispatch("GET", "/api/state", {})[0], 400)

    def test_unknown_get_route_and_malformed_state_query_have_v2_codes(self) -> None:
        self.assertEqual(SERVER.dispatch("GET", "/api/unknown", {})[0], 404)
        self.assertEqual(SERVER.dispatch("GET", "/api/state?target_id=a&target_id=b", {})[0], 400)

    def test_state_error_mapping_is_input_400_or_storage_503(self) -> None:
        with mock.patch.object(SERVER, "seo_employee_state", return_value=json.dumps({"status": "error", "error": {"code": "SEO_TARGET_NOT_FOUND"}})):
            self.assertEqual(SERVER.dispatch("GET", "/api/state?target_id=target-other-01234567", {})[0], 400)
        with mock.patch.object(SERVER, "seo_employee_state", return_value=json.dumps({"state": "failed", "last_error": {"code": "SEO_CONFIGURATION_INVALID"}})):
            self.assertEqual(SERVER.dispatch("GET", "/api/state", {})[0], 503)

    def test_handler_enforces_auth_body_cap_transfer_encoding_and_routes(self) -> None:
        self.assertEqual(self._request("POST", "/api/run", body=b"{}"), 401)
        headers = {"Authorization": "Bearer " + "x" * 32, "Content-Length": str(SERVER.MAX_BODY_BYTES + 1)}
        self.assertEqual(self._request("POST", "/api/run", headers=headers, body=b"x" * (SERVER.MAX_BODY_BYTES + 1)), 400)
        self.assertEqual(self._request("POST", "/api/run", headers={"Authorization": "Bearer " + "x" * 32, "Transfer-Encoding": "chunked"}, body=b"{}"), 400)
        self.assertEqual(self._request("GET", "/api/unknown", headers={"X-API-KEY": "x" * 32}), 404)
        self.assertEqual(self._request("GET", "/api/state?", headers={"X-API-KEY": "x" * 32}), 400)

    def test_targets_route_returns_target_summaries(self) -> None:
        with mock.patch.object(SERVER, "list_target_states", return_value={"status": "success", "targets": []}):
            self.assertEqual(SERVER.dispatch("GET", "/api/targets", {}), (200, {"status": "success", "targets": []}))

    def test_targets_reject_query_and_report_backend_failure_as_503(self) -> None:
        self.assertEqual(SERVER.dispatch("GET", "/api/targets?x=1", {})[0], 400)
        with mock.patch.object(SERVER, "list_target_states", return_value={"status": "error", "error": {"code": "SEO_CONFIGURATION_INVALID"}}):
            self.assertEqual(SERVER.dispatch("GET", "/api/targets", {})[0], 503)

    def test_handler_maps_backend_failures_to_503_without_exposing_details(self) -> None:
        headers = {"Authorization": "Bearer " + "x" * 32, "Content-Type": "application/json"}
        with mock.patch.object(SERVER, "seo_employee_run", side_effect=OSError("disk path secret")):
            self.assertEqual(self._request("POST", "/api/configure", headers=headers, body=b"{}"), 503)
        with mock.patch.object(SERVER, "seo_employee_run", return_value="{bad-json"):
            self.assertEqual(self._request("POST", "/api/run", headers=headers, body=b'{"target_id":"target-example-com-0f115db0"}'), 503)

    def test_handler_distinguishes_client_input_from_state_targets_and_configure_backend_failures(self) -> None:
        headers = {"Authorization": "Bearer " + "x" * 32, "Content-Type": "application/json"}
        self.assertEqual(self._request("POST", "/api/run", headers=headers, body=b"{bad"), 400)
        with mock.patch.object(SERVER, "seo_employee_run", return_value=json.dumps({"status": "error", "error": {"code": "SEO_CONFIGURATION_UNAVAILABLE"}})):
            self.assertEqual(self._request("POST", "/api/configure", headers=headers, body=b"{}"), 503)
        with mock.patch.object(SERVER, "seo_employee_state", return_value="{bad"):
            self.assertEqual(self._request("GET", "/api/state?target_id=target-example-com-0f115db0", headers={"X-API-KEY": "x" * 32}), 503)
        with mock.patch.object(SERVER, "list_target_states", return_value=[]):
            self.assertEqual(self._request("GET", "/api/targets", headers={"X-API-KEY": "x" * 32}), 503)
        with mock.patch.object(SERVER, "list_target_states", side_effect=RuntimeError("internal")):
            self.assertEqual(self._request("GET", "/api/targets", headers={"X-API-KEY": "x" * 32}), 503)
        self.assertEqual(self._request("POST", "/api/run?broken", headers=headers, body=b"{}"), 400)

    def test_execution_failure_is_the_only_503_case(self) -> None:
        with mock.patch.object(SERVER, "seo_employee_run", return_value=json.dumps({"status": "error", "error": {"code": "ownership_confirmation_required"}})):
            self.assertEqual(SERVER.dispatch("POST", "/api/run", {"target_id": "target-example-com-0f115db0"})[0], 400)
        with mock.patch.object(SERVER, "seo_employee_run", return_value=json.dumps({"status": "error", "error": {"code": "SEO_RUN_FAILED"}})):
            self.assertEqual(SERVER.dispatch("POST", "/api/run", {"target_id": "target-example-com-0f115db0"})[0], 503)
        with mock.patch.object(SERVER, "seo_employee_run", return_value=json.dumps({"status": "error", "error": {"code": "SEO_QUEUE_UNAVAILABLE"}})):
            self.assertEqual(SERVER.dispatch("POST", "/api/run", {"target_id": "target-example-com-0f115db0"})[0], 503)


if __name__ == "__main__":
    unittest.main()
