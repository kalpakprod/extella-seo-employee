#!/usr/bin/env python3
"""Test-local HTTP origin/provider fixture for the P2 process-boundary tests."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class FixtureState:
    def __init__(self, site_mode: str, provider_mode: str, event_log: str) -> None:
        self.condition = threading.Condition()
        self.site_mode = site_mode
        self.provider_mode = provider_mode
        self.active_site = 0
        self.active_provider = 0
        self.events: list[dict[str, object]] = []
        self.event_log = open(event_log, "a", encoding="utf-8") if event_log else None

    def record(self, event: str, **fields: object) -> None:
        item: dict[str, object] = {
            "event": event,
            "wall_ns": time.time_ns(),
            "monotonic_ns": time.monotonic_ns(),
            "pid": os.getpid(),
        }
        item.update(fields)
        with self.condition:
            self.events.append(item)
            if self.event_log is not None:
                json.dump(item, self.event_log, separators=(",", ":"))
                self.event_log.write("\n")
                self.event_log.flush()
            self.condition.notify_all()

    def snapshot(self) -> dict[str, object]:
        with self.condition:
            return {
                "site_mode": self.site_mode,
                "provider_mode": self.provider_mode,
                "active_site": self.active_site,
                "active_provider": self.active_provider,
                "events": list(self.events),
            }

    def configure(self, site_mode: str | None, provider_mode: str | None) -> None:
        if site_mode is not None and site_mode not in {"fast", "slow"}:
            raise ValueError("site mode must be fast or slow")
        if provider_mode is not None and provider_mode not in {"fast", "drip"}:
            raise ValueError("provider mode must be fast or drip")
        with self.condition:
            if site_mode is not None:
                self.site_mode = site_mode
            if provider_mode is not None:
                self.provider_mode = provider_mode
            self.condition.notify_all()

    def condition_met(self, name: str, count: int) -> bool:
        if name == "site_active":
            return self.active_site >= count
        if name == "site_idle":
            return self.active_site == 0
        if name == "provider_active":
            return self.active_provider >= count
        if name == "all_idle":
            return self.active_site == 0 and self.active_provider == 0
        return sum(item.get("event") == name for item in self.events) >= count

    def wait_for(self, name: str, count: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.condition_met(name, count):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def close(self) -> None:
        if self.event_log is not None:
            self.event_log.close()


STATE: FixtureState


def _drip(target: object, event: str, label: str) -> None:
    write = getattr(target, "write", None)
    flush = getattr(target, "flush", None)
    if not callable(write):
        raise TypeError("drip target has no write method")
    for index in range(2_000):
        write(b"x")
        if callable(flush):
            flush()
        if index in {0, 1} or index % 16 == 0:
            STATE.record(event, label=label, index=index + 1)
        time.sleep(0.02)


def _provider_payload(source: str, original_path: str) -> object:
    if source == "nu":
        return {"messages": []}
    if source == "tls":
        return {"status": "READY", "endpoints": []}
    if source == "cc" and original_path.endswith("/collinfo.json"):
        return [{"id": "CC-MAIN-2026-33"}]
    if source == "cc" and "-index?" in original_path:
        return []
    STATE.record("provider_unknown_request", source=source, path=original_path)
    return {}


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "ExtellaP2AcceptanceFixture/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.startswith("/control/"):
            self._control()
            return
        if self.path.startswith("/provider"):
            self._provider()
            return
        self._site()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.startswith("/provider"):
            self._provider()
            return
        self._json(404, {"status": "error", "code": "route_not_found"})

    def _site(self) -> None:
        with STATE.condition:
            mode = STATE.site_mode
            STATE.active_site += 1
            STATE.condition.notify_all()
        STATE.record("site_started", path=self.path, mode=mode, client_port=self.client_address[1])
        body = b"<html><head></head><body>fixture</body></html>"
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(65_536 if mode == "slow" else len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if mode == "slow":
                _drip(self.wfile, "site_body_byte", self.path)
            else:
                self.wfile.write(body)
                self.wfile.flush()
                STATE.record("site_completed", path=self.path)
        except (BrokenPipeError, ConnectionResetError, OSError) as error:
            STATE.record("site_client_closed", path=self.path, error=type(error).__name__)
        finally:
            with STATE.condition:
                STATE.active_site -= 1
                STATE.condition.notify_all()
            STATE.record("site_closed", path=self.path)

    def _provider(self) -> None:
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        source = query.get("source", [""])[0]
        original_path = query.get("path", [""])[0]
        with STATE.condition:
            mode = STATE.provider_mode
            STATE.active_provider += 1
            STATE.condition.notify_all()
        STATE.record("provider_started", source=source, path=original_path, mode=mode)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 0:
                self.rfile.read(min(length, 65_536))
            if mode == "drip":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "65_536")
                self.send_header("Connection", "close")
                self.end_headers()
                _drip(self.wfile, "provider_body_byte", source)
            else:
                self._json(200, _provider_payload(source, original_path))
                STATE.record("provider_completed", source=source, path=original_path)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError) as error:
            STATE.record("provider_client_closed", source=source, error=type(error).__name__)
        finally:
            with STATE.condition:
                STATE.active_provider -= 1
                STATE.condition.notify_all()
            STATE.record("provider_closed", source=source)

    def _control(self) -> None:
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/control/state":
                self._json(200, STATE.snapshot())
                return
            if parsed.path == "/control/config":
                STATE.configure(query.get("site", [None])[0], query.get("provider", [None])[0])
                self._json(200, STATE.snapshot())
                return
            if parsed.path == "/control/wait":
                name = query.get("event", [""])[0]
                count = int(query.get("count", ["1"])[0])
                timeout_ms = int(query.get("timeout_ms", ["3000"])[0])
                if not name or count < 1 or not 1 <= timeout_ms <= 30_000:
                    raise ValueError("wait arguments are invalid")
                matched = STATE.wait_for(name, count, timeout_ms / 1000)
                self._json(200 if matched else 504, STATE.snapshot() | {"matched": matched})
                return
            self._json(404, {"status": "error", "code": "route_not_found"})
        except (TypeError, ValueError) as error:
            self._json(400, {"status": "error", "code": "invalid_request", "detail": str(error)})

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-log", required=True)
    parser.add_argument("--site-mode", choices=("fast", "slow"), default="slow")
    parser.add_argument("--provider-mode", choices=("fast", "drip"), default="drip")
    args = parser.parse_args()

    global STATE
    STATE = FixtureState(args.site_mode, args.provider_mode, args.event_log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, name="fixture-http", daemon=True)
    thread.start()
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()
        server.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"ready": True, "pid": os.getpid(), "port": server.server_port}, separators=(",", ":")), flush=True)
    STATE.record("fixture_ready", port=server.server_port)
    stopped.wait()
    server.server_close()
    STATE.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
