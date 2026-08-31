#!/usr/bin/env python3
"""Loopback-bound HTTP facade and daily scheduler for the Docker deployment."""

from __future__ import annotations

import hmac
import json
import os
import pathlib
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PRODUCT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT))
sys.path.insert(0, str(PRODUCT_ROOT / "experts"))

from seo_employee_run import CONFIG_PATH as RUN_CONFIG_PATH, QUEUE_PATH, QueueConsumer, SeoEmployeeQueue, seo_employee_run  # noqa: E402
from seo_employee_schedule import CONFIG_PATH, seo_employee_schedule  # noqa: E402
from seo_employee_state import list_target_states, seo_employee_state  # noqa: E402


VERSION = "2.0.0"
MAX_BODY_BYTES = 16_384
TOKEN_FILE = pathlib.Path(os.environ.get("EXTELLA_SEO_API_TOKEN_FILE", "/run/secrets/seo_employee_api_token"))
RUN_DEADLINE_SECONDS = 180.0
QUEUE_CONSUMER: QueueConsumer | None = None


class ClientInputError(ValueError):
    pass


class BackendResponseError(RuntimeError):
    pass


def _backend_payload(route: str, value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("status") not in {"success", "error", "partial"}:
        raise BackendResponseError("invalid execution response")
    if value["status"] == "error":
        error = value.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            raise BackendResponseError("invalid execution response")
        return value
    if route == "/api/run" and value.get("state") == "queued":
        if value.get("method") != "run" or not isinstance(value.get("queue_item"), dict):
            raise BackendResponseError("invalid execution response")
    elif route == "/api/configure":
        if value.get("method") != "configure" or not isinstance(value.get("config"), dict):
            raise BackendResponseError("invalid execution response")
    elif route == "/api/preflight":
        if value.get("method") != "preflight" or value.get("result") != "ok":
            raise BackendResponseError("invalid execution response")
    elif value.get("state") not in {"partial", "duplicate"}:
        raise BackendResponseError("invalid execution response")
    return value


def read_token(path: pathlib.Path = TOKEN_FILE) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("SEO Employee API token is missing or too short")
    return token


def dispatch(method: str, path: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    try:
        parsed = urllib.parse.urlsplit(path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ClientInputError("invalid request") from error
    if "?" in path and not parsed.query:
        return 400, {"status": "error", "code": "invalid_request"}
    if method == "GET" and parsed.path not in {"/api/targets", "/api/state", "/health"}:
        return 404, {"status": "error", "code": "route_not_found"}
    if method == "GET" and parsed.path == "/api/targets":
        if query:
            return 400, {"status": "error", "code": "invalid_request"}
        payload = list_target_states()
        if not isinstance(payload, dict):
            raise BackendResponseError("invalid target response")
        return (503 if payload.get("status") == "error" else 200), payload
    if method == "GET" and parsed.path == "/api/state":
        if set(query) - {"target_id"} or len(query.get("target_id", [])) > 1:
            return 400, {"status": "error", "code": "invalid_request"}
        target_id = query.get("target_id", [None])[0] or None
        if target_id is None and len(list_target_states().get("targets", [])) > 1:
            return 400, {"status": "error", "code": "target_id_required"}
        try:
            payload = json.loads(seo_employee_state(target_id=target_id))
        except (OSError, RuntimeError, TypeError, AttributeError, json.JSONDecodeError) as error:
            raise BackendResponseError("invalid state response") from error
        if not isinstance(payload, dict):
            raise BackendResponseError("invalid state response")
        error = payload.get("error") if isinstance(payload.get("error"), dict) else payload.get("last_error")
        code = error.get("code") if isinstance(error, dict) else ""
        if code == "SEO_TARGET_NOT_FOUND":
            return 400, payload
        if code in {"SEO_CONFIGURATION_INVALID", "SEO_STATE_INVALID", "SEO_STATE_UNAVAILABLE"}:
            return 503, payload
        return 200, payload
    if method != "POST":
        return 405, {"status": "error", "code": "method_not_allowed"}
    if query:
        return 400, {"status": "error", "code": "invalid_request"}
    configure_fields = {
        "target_id", "target_name", "site_url", "profile", "language", "region", "site_type", "business_goal",
        "daily_run_time", "timezone", "max_pages", "mode", "ownership_confirmed",
    }
    if parsed.path == "/api/configure" and set(body) <= configure_fields:
        result = seo_employee_run(
            method="configure",
            target_id=str(body.get("target_id", "")),
            target_name=str(body.get("target_name", "")),
            site_url=str(body.get("site_url", "")),
            profile=str(body.get("profile", "service_b2b")),
            language=str(body.get("language", "ru")),
            region=str(body.get("region", "GLOBAL")),
            site_type=str(body.get("site_type", "website")),
            business_goal=str(body.get("business_goal", "organic_visibility")),
            daily_run_time=str(body.get("daily_run_time", "")),
            timezone=str(body.get("timezone", "")),
            max_pages=body.get("max_pages", 25),
            mode=str(body.get("mode", "")),
            ownership_confirmed=body.get("ownership_confirmed", False),
        )
    elif parsed.path == "/api/run" and set(body) <= {"target_id", "mode"} and isinstance(body.get("target_id"), str):
        run_kwargs: dict[str, object] = {
            "method": "run",
            "target_id": str(body["target_id"]),
            "mode": str(body.get("mode", "")),
            "trigger": "manual",
        }
        if QUEUE_CONSUMER is not None:
            run_kwargs["queue_consumer"] = QUEUE_CONSUMER
        result = seo_employee_run(**run_kwargs)
    elif parsed.path == "/api/preflight" and not body:
        result = seo_employee_run(method="preflight")
    else:
        if parsed.path not in {"/api/configure", "/api/run", "/api/preflight"}:
            return 404, {"status": "error", "code": "route_not_found"}
        return 400, {"status": "error", "code": "invalid_request"}
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError) as error:
        raise BackendResponseError("invalid execution response") from error
    payload = _backend_payload(parsed.path, payload)
    state = payload.get("state")
    if state in {"queued", "duplicate"}:
        return 202, payload
    if state in {"ready", "partial"} or payload.get("status") == "success":
        return 200, payload
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else ""
    if code in {"SEO_RUN_INPUT_INVALID", "SEO_CONFIGURATION_INVALID", "SEO_METHOD_UNSUPPORTED", "ownership_confirmation_required"}:
        return 400, payload
    if code in {"SEO_RUN_FAILED", "SEO_QUEUE_UNAVAILABLE", "SEO_CONFIGURATION_UNAVAILABLE", "SEO_PREFLIGHT_FAILED"}:
        return 503, payload
    return 503 if state == "failed" else 400, payload


def handler(api_token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ExtellaSEOEmployee/2.0"

        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            bearer = self.headers.get("Authorization", "")
            supplied = bearer[7:] if bearer.startswith("Bearer ") else self.headers.get("X-API-KEY", "")
            return bool(supplied) and hmac.compare_digest(supplied, api_token)

        def _body(self) -> dict[str, object]:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("transfer encoding is not supported")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("invalid content length") from error
            if length < 0:
                raise ValueError("request body is too large")
            if length > MAX_BODY_BYTES:
                # Drain one bounded request before responding so a local client
                # receives the explicit 400 instead of a reset connection.
                self.rfile.read(min(length, MAX_BODY_BYTES + 1))
                raise ValueError("request body is too large")
            if not length:
                return {}
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            return payload

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"status": "ok", "version": VERSION})
                return
            if not self._authorized():
                self._send(401, {"status": "error", "code": "unauthorized"})
                return
            try:
                status, payload = dispatch("GET", self.path, {})
            except ClientInputError:
                status, payload = 400, {"status": "error", "code": "invalid_request"}
            except (BackendResponseError, OSError, RuntimeError, TypeError, AttributeError, json.JSONDecodeError):
                status, payload = 503, {"status": "error", "code": "state_unavailable"}
            self._send(status, payload)

        def do_POST(self) -> None:
            if not self._authorized():
                self._send(401, {"status": "error", "code": "unauthorized"})
                return
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError):
                status, payload = 400, {"status": "error", "code": "invalid_request"}
            else:
                try:
                    status, payload = dispatch("POST", self.path, body)
                    if not isinstance(payload, dict):
                        raise BackendResponseError("invalid execution response")
                except ClientInputError:
                    status, payload = 400, {"status": "error", "code": "invalid_request"}
                except (BackendResponseError, OSError, RuntimeError, TypeError, AttributeError, json.JSONDecodeError):
                    status, payload = 503, {"status": "error", "code": "execution_unavailable"}
            self._send(status, payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def schedule_loop(stop: threading.Event, wait_seconds: float = 60.0) -> None:
    while not stop.is_set():
        if CONFIG_PATH.is_file():
            try:
                result = json.loads(seo_employee_schedule())
                if result.get("status") == "error":
                    print(json.dumps({"status": "error", "code": "schedule_check_failed"}), file=sys.stderr)
            except (OSError, ValueError, json.JSONDecodeError):
                print(json.dumps({"status": "error", "code": "schedule_check_failed"}), file=sys.stderr)
        stop.wait(wait_seconds)


def main() -> int:
    try:
        api_token = read_token()
        port = int(os.environ.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (OSError, RuntimeError, ValueError):
        print(json.dumps({"status": "error", "code": "startup_configuration_invalid"}))
        return 1
    stop = threading.Event()
    global QUEUE_CONSUMER
    try:
        QUEUE_CONSUMER = QueueConsumer(queue=SeoEmployeeQueue(QUEUE_PATH), config_path=RUN_CONFIG_PATH)
        QUEUE_CONSUMER.start()
    except (OSError, ValueError):
        print(json.dumps({"status": "error", "code": "startup_queue_unavailable"}))
        return 1
    scheduler = threading.Thread(target=schedule_loop, args=(stop,), daemon=True)
    scheduler.start()
    server = ThreadingHTTPServer(("0.0.0.0", port), handler(api_token))
    try:
        server.serve_forever()
    finally:
        stop.set()
        if QUEUE_CONSUMER is not None:
            QUEUE_CONSUMER.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
