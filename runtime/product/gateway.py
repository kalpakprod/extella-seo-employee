#!/usr/bin/env python3
"""Fixed-route HTTP gateway used at the two Docker network boundaries."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MAX_BODY_BYTES = 65_536
MAX_RESPONSE_BYTES = 2_000_000
RUN_DEADLINE_SECONDS = 180.0
ROUTES = {
    "product": {
        ("GET", "/health"),
        ("GET", "/api/state"),
        ("GET", "/api/targets"),
        ("POST", "/api/configure"),
        ("POST", "/api/preflight"),
        ("POST", "/api/run"),
    },
    "agent-zero": {("POST", "/api/api_message")},
}
TARGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
HOSTS = {
    "product": {"seo-employee"},
    "agent-zero": {"agent-zero", "host.docker.internal"},
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def validate_upstream(mode: str, value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        mode not in ROUTES
        or parsed.scheme != "http"
        or parsed.hostname not in HOSTS[mode]
        or parsed.port is None
        or parsed.path not in {"", "/"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("proxy upstream is invalid")
    return value.rstrip("/")


def request_target(mode: str, method: str, value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("proxy route is invalid")
    if (method, parsed.path) not in ROUTES.get(mode, set()):
        raise ValueError("proxy route is invalid")
    if not parsed.query:
        return parsed.path
    if mode != "product" or method != "GET" or parsed.path != "/api/state":
        raise ValueError("proxy query is invalid")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    values = query.get("target_id", [])
    if set(query) != {"target_id"} or len(values) != 1 or not TARGET_ID_RE.fullmatch(values[0]):
        raise ValueError("proxy query is invalid")
    return parsed.path + "?" + urllib.parse.urlencode({"target_id": values[0]})


def handler(mode: str, upstream: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _proxy(self) -> None:
            try:
                target = request_target(mode, self.command, self.path)
            except ValueError:
                self._send(404, b'{"status":"error","code":"route_not_found"}')
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > MAX_BODY_BYTES:
                    raise ValueError
                body = self.rfile.read(length) if length else None
                headers = {
                    name: value
                    for name in ("Content-Type", "Authorization", "X-API-KEY")
                    if (value := self.headers.get(name))
                }
                request = urllib.request.Request(
                    upstream + target,
                    data=body,
                    headers=headers,
                    method=self.command,
                )
                try:
                    response = OPENER.open(request, timeout=RUN_DEADLINE_SECONDS)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    result = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(result) > MAX_RESPONSE_BYTES:
                        raise ValueError
                    self._send(response.status, result)
            except (OSError, ValueError, urllib.error.URLError):
                self._send(502, b'{"status":"error","code":"upstream_unavailable"}')

        do_GET = _proxy
        do_POST = _proxy

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def main() -> int:
    try:
        mode = os.environ["EXTELLA_PROXY_MODE"]
        upstream = validate_upstream(mode, os.environ["EXTELLA_PROXY_UPSTREAM"])
        port = int(os.environ.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (KeyError, ValueError):
        print(json.dumps({"status": "error", "code": "proxy_configuration_invalid"}))
        return 1
    server = ThreadingHTTPServer(("0.0.0.0", port), handler(mode, upstream))
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
