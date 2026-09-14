#!/usr/bin/env python3
"""Fixed-route HTTP gateway used at the two Docker network boundaries."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MAX_BODY_BYTES = 65_536
MAX_RESPONSE_BYTES = 2_000_000
RUN_DEADLINE_SECONDS = 180.0
DEFAULT_RATE_LIMIT_PER_MINUTE = 120
RATE_LIMIT_WINDOW_SECONDS = 60.0
MAX_TRACKED_CLIENTS = 4096


def parse_rate_limit(value: str | None) -> int:
    if value is None or not value.strip():
        return DEFAULT_RATE_LIMIT_PER_MINUTE
    try:
        limit = int(value.strip(), 10)
    except ValueError:
        raise ValueError("rate limit is invalid") from None
    if limit < 1:
        raise ValueError("rate limit is invalid")
    return limit


class RateLimiter:
    """Per-client fixed-window limiter. Rejected routes count too."""

    def __init__(self, per_minute: int, window_seconds: float = RATE_LIMIT_WINDOW_SECONDS) -> None:
        self.per_minute = per_minute
        self.window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > MAX_TRACKED_CLIENTS:
                self._hits = {
                    tracked: seen
                    for tracked, seen in self._hits.items()
                    if seen[0] + self.window > now
                }
            window_start, count = self._hits.get(key, (now, 0))
            if window_start + self.window <= now:
                window_start, count = now, 0
            count += 1
            self._hits[key] = [window_start, count]
            if count > self.per_minute:
                return False, max(1, int(window_start + self.window - now))
            return True, 0


def access_record(method: str, target: str, status: int, client: str) -> str:
    try:
        route = urllib.parse.urlsplit(target).path or "-"
    except ValueError:
        route = "-"
    return json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client": client,
        "method": method,
        "route": route,
        "status": status,
    }, ensure_ascii=False)
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


def handler(
    mode: str, upstream: str, rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
) -> type[BaseHTTPRequestHandler]:
    limiter = RateLimiter(rate_limit_per_minute)

    class Handler(BaseHTTPRequestHandler):
        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str = "application/json",
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
            try:
                sys.stderr.write(
                    access_record(self.command, self.path, status, self.client_address[0]) + "\n"
                )
                sys.stderr.flush()
            except OSError:
                pass

        def _proxy(self) -> None:
            allowed, retry_after = limiter.allow(self.client_address[0])
            if not allowed:
                self._send(
                    429,
                    b'{"status":"error","code":"rate_limited"}',
                    extra_headers={"Retry-After": str(retry_after)},
                )
                return
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
        rate_limit = parse_rate_limit(os.environ.get("EXTELLA_RATE_LIMIT_PER_MINUTE"))
        port = int(os.environ.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (KeyError, ValueError):
        print(json.dumps({"status": "error", "code": "proxy_configuration_invalid"}))
        return 1
    server = ThreadingHTTPServer(("0.0.0.0", port), handler(mode, upstream, rate_limit))
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
