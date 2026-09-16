#!/usr/bin/env python3
"""PageSpeed/CrUX + site-files probe worker.

This is the only egress point of the PSI lane: it fetches Google PageSpeed and
CrUX data plus bounded site files (robots.txt, sitemap.xml, homepage HTML) and
returns a strict extella.psi_source.v1 payload. Thresholds and verdicts live in
the in-product PSIAdapter, never here.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import selectors
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ElementTree
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PSI_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_API = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
GOOGLE_HOSTS = {"www.googleapis.com", "chromeuxreport.googleapis.com"}
MAX_URLS = 3
FETCH_BYTES = {"robots_txt": 65_536, "sitemap_xml": 65_536, "homepage_html": 262_144}
CALL_TIMEOUT_SECONDS = 60.0
MAX_BODY_BYTES = 65_536
MAX_PINNED_ADDRESSES = 4
MAX_TIMEOUT_MS = 720_000
MAX_CHILDREN = 2
MAX_HANDLERS = 4
CHILD_IPC_BYTES = 65_536
CHILD_CLEANUP_SECONDS = 0.25
HANDLER_IDLE_SECONDS = 2.0
CHILD_MODE = "--psi-child"
PORT = int(os.environ.get("PORT", "8084"))

_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_USER_AGENT = "ExtellaPSI/2.1"

_LCP_KEYS = ("largest-contentful-paint",)
_INP_KEYS = ("interaction-to-next-paint",)
_CLS_KEYS = ("cumulative-layout-shift",)
_FIELD_KEYS = {
    "lcp": "LARGEST_CONTENTFUL_PAINT_MS",
    "inp": "INTERACTION_TO_NEXT_PAINT",
    "cls": "CUMULATIVE_LAYOUT_SHIFT",
}


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("fetch deadline exceeded")
    return remaining


def _is_allowed_address(value: str) -> bool:
    if os.environ.get("PSI_ALLOW_PRIVATE") == "1":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address in _NAT64_PREFIX:
        embedded = ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF)
        if not embedded.is_global:
            return False
    return bool(address.is_global)


def _global_addresses(hostname: str, port: int) -> list[str]:
    """Resolve once and require every answer to be a global address."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError("fetch target is not a global address") from error
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses or not all(_is_allowed_address(address) for address in addresses):
        raise ValueError("fetch target is not a global address")
    return addresses


def _host_header(hostname: str, port: int | None) -> str:
    authority = f"[{hostname}]" if ":" in hostname else hostname
    return authority if port is None else f"{authority}:{port}"


def _prepare_socket_stage(connection: object, deadline: float) -> None:
    """Refresh the socket timeout immediately before one blocking HTTP stage."""
    remaining = _remaining(deadline)
    sock = getattr(connection, "sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if callable(settimeout):
        settimeout(remaining)


def _read_response_body(response: object, connection: object, max_bytes: int, deadline: float) -> bytes:
    limit = max_bytes + 1
    if not isinstance(response, http.client.HTTPResponse):
        _prepare_socket_stage(connection, deadline)
        raw = response.read(limit)
        _remaining(deadline)
        if not isinstance(raw, bytes):
            raise ValueError("HTTP response body is invalid")
        return raw
    length = getattr(response, "length", None)
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        amount = min(4_096, limit - total)
        _prepare_socket_stage(connection, deadline)
        chunk = response.read(amount)
        _remaining(deadline)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise ValueError("HTTP response body is invalid")
        chunks.append(chunk)
        total += len(chunk)
        if isinstance(length, int) and total >= length:
            break
    return b"".join(chunks)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address
        self._deadline: float | None = None

    def connect(self) -> None:
        timeout = self.timeout if self._deadline is None else _remaining(self._deadline)
        self.sock = socket.create_connection((self._address, self.port), timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address
        self._deadline: float | None = None

    def connect(self) -> None:
        timeout = self.timeout if self._deadline is None else _remaining(self._deadline)
        raw = socket.create_connection((self._address, self.port), timeout)
        try:
            if self._deadline is not None:
                raw.settimeout(_remaining(self._deadline))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _same_origin(left: str, right: str) -> bool:
    first, second = urllib.parse.urlsplit(left), urllib.parse.urlsplit(right)
    return (first.scheme, first.hostname, first.port) == (second.scheme, second.hostname, second.port)


def fetch_bytes(url: str, timeout: float, max_bytes: int, *, allowed_hosts: set[str]) -> tuple[int, bytes, bool]:
    """Fetch with same-origin-or-allowlist guard, capped bytes, manual redirects.

    The validated numeric address is pinned into the socket connect, so DNS
    cannot be re-resolved between the policy check and the connection.
    """
    if max_bytes < 1 or timeout <= 0:
        raise ValueError("fetch budget is invalid")
    deadline = time.monotonic() + timeout
    current = url
    for _ in range(4):
        parsed = urllib.parse.urlsplit(current)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.hostname not in allowed_hosts
        ):
            raise ValueError("fetch target is not allowed")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        _remaining(deadline)
        addresses = _global_addresses(parsed.hostname, port)
        _remaining(deadline)
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        headers = {"Host": _host_header(parsed.hostname, parsed.port), "User-Agent": _USER_AGENT}
        connection_class = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
        last_error: Exception | None = None
        for address in addresses[:MAX_PINNED_ADDRESSES]:
            _remaining(deadline)
            connection = None
            response_obtained = False
            try:
                connection = connection_class(parsed.hostname, port, address, _remaining(deadline))
                connection._deadline = deadline
                _prepare_socket_stage(connection, deadline)
                connection.request("GET", path, headers=headers)
                _prepare_socket_stage(connection, deadline)
                response = connection.getresponse()
                response_obtained = True
                _prepare_socket_stage(connection, deadline)
                status = response.status
                if status in _REDIRECT_CODES:
                    location = response.getheader("Location", "") or ""
                    target = urllib.parse.urljoin(current, location)
                    if not location or not _same_origin(target, current):
                        raise ValueError("redirect leaves the probed origin")
                    current = target
                    break
                if status != 200:
                    return status, b"", False
                raw = _read_response_body(response, connection, max_bytes, deadline)
                if len(raw) > max_bytes:
                    return status, raw[:max_bytes], True
                return status, raw, False
            except http.client.HTTPException:
                raise
            except (OSError, TimeoutError) as error:
                if response_obtained:
                    raise
                last_error = error
                _remaining(deadline)
            finally:
                if connection is not None:
                    connection.close()
        else:
            if last_error is not None:
                raise last_error
            raise OSError("fetch target has no reachable public address")
        _remaining(deadline)
    raise ValueError("too many redirects")


def _audit_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")} or number < 0:
        return None
    return number


def extract_lab_metrics(psi_json: object) -> dict[str, float | None]:
    """Worst lab value per metric across the strategies present in one response."""
    found: dict[str, float | None] = {"lcp": None, "cls": None, "inp": None}
    if not isinstance(psi_json, dict):
        return found
    audits = psi_json.get("lighthouseResult", {}).get("audits") if isinstance(psi_json.get("lighthouseResult"), dict) else None
    if not isinstance(audits, dict):
        return found
    for metric, keys in (("lcp", _LCP_KEYS), ("inp", _INP_KEYS), ("cls", _CLS_KEYS)):
        for key in keys:
            audit = audits.get(key)
            value = _audit_number(audit.get("numericValue")) if isinstance(audit, dict) else None
            if value is not None and (found[metric] is None or value > found[metric]):
                found[metric] = value
    return found


def extract_loading_experience(psi_json: object) -> dict[str, float | None]:
    found: dict[str, float | None] = {"lcp": None, "cls": None, "inp": None}
    if not isinstance(psi_json, dict):
        return found
    experience = psi_json.get("loadingExperience")
    metrics = experience.get("metrics") if isinstance(experience, dict) else None
    if not isinstance(metrics, dict):
        return found
    for metric, key in _FIELD_KEYS.items():
        entry = metrics.get(key)
        value = _audit_number(entry.get("percentile")) if isinstance(entry, dict) else None
        found[metric] = value
    return found


def extract_crux_origin(crux_json: object) -> dict[str, float | None]:
    found: dict[str, float | None] = {"lcp": None, "cls": None, "inp": None}
    if not isinstance(crux_json, dict):
        return found
    metrics = crux_json.get("record", {}).get("metrics") if isinstance(crux_json.get("record"), dict) else None
    if not isinstance(metrics, dict):
        return found
    for metric, key in _FIELD_KEYS.items():
        entry = metrics.get(key)
        percentiles = entry.get("percentiles") if isinstance(entry, dict) else None
        raw = percentiles.get("p75") if isinstance(percentiles, dict) else None
        try:
            found[metric] = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            found[metric] = None
        if found[metric] is not None and (found[metric] != found[metric] or found[metric] < 0):
            found[metric] = None
    return found


def sample_urls(site_url: str, sitemap_xml: bytes, max_urls: int) -> list[str]:
    """Homepage plus same-origin sitemap locs in document order, capped."""
    urls = [site_url]
    if max_urls > 1 and sitemap_xml:
        try:
            root = ElementTree.fromstring(sitemap_xml)
        except ElementTree.ParseError:
            root = None
        if root is not None:
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1].lower() != "loc" or not element.text:
                    continue
                candidate = element.text.strip()
                if candidate not in urls and _same_origin(candidate, site_url):
                    urls.append(candidate)
                if len(urls) >= max_urls:
                    break
    return urls[:max_urls]


def _reason_for(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 403:
            return "http_403"
        if error.code == 429:
            return "http_429"
        if error.code == 503:
            return "http_503"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "http_503"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://googleapis.invalid/", code, "error payload", {}, None)


def _error_code(payload: object) -> int | None:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        code = payload["error"].get("code")
        if isinstance(code, int):
            return code
    return None


def _log_api_error(api: str, url_index: int, strategy: str, detail: str) -> None:
    sys.stderr.write(f"psi {api} error url_index={url_index} strategy={strategy} detail={detail}\n")
    sys.stderr.flush()


def run_probe(
    site_url: str,
    max_urls: int,
    timeout_ms: int,
    api_key: str,
    *,
    now: float | None = None,
) -> dict[str, object]:
    parsed = urllib.parse.urlsplit(site_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("site url is invalid")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = {parsed.hostname}
    deadline = (now if now is not None else time.monotonic()) + timeout_ms / 1000
    sitefiles: dict[str, dict[str, object]] = {}
    for section, path in (("robots_txt", "/robots.txt"), ("sitemap_xml", "/sitemap.xml"), ("homepage_html", "/")):
        target = origin + path if section != "homepage_html" else site_url
        try:
            remaining = max(1.0, min(CALL_TIMEOUT_SECONDS, deadline - time.monotonic()))
            status, body, truncated = fetch_bytes(target, remaining, FETCH_BYTES[section], allowed_hosts=allowed | GOOGLE_HOSTS)
            text = body.decode("utf-8", errors="replace")
        except (ValueError, OSError, urllib.error.URLError) as error:
            if isinstance(error, ValueError):
                raise
            sitefiles[section] = {"http_status": 0, "truncated": False, "content": ""}
            continue
        sitefiles[section] = {"http_status": status, "truncated": truncated, "content": text}
    sitemap_body = b""
    if sitefiles["sitemap_xml"]["http_status"] == 200 and not sitefiles["sitemap_xml"]["truncated"]:
        sitemap_body = str(sitefiles["sitemap_xml"]["content"]).encode("utf-8", errors="replace")
    probed = sample_urls(site_url, sitemap_body, max_urls)
    metrics: list[dict[str, object]] = []
    crux: dict[str, float | None] | None = None
    crux_block: dict[str, object] = {"status": "not_configured", "reason": None}
    if api_key:
        try:
            remaining = max(1.0, min(CALL_TIMEOUT_SECONDS, deadline - time.monotonic()))
            crux_response = _post_json(
                f"{CRUX_API}?key={urllib.parse.quote(api_key, safe='')}",
                {"origin": origin}, remaining,
            )
        except (OSError, ValueError, urllib.error.URLError) as error:
            _log_api_error("crux", -1, "-", type(error).__name__)
            crux_block = {"status": "unavailable", "reason": _reason_for(error)}
        else:
            code = _error_code(crux_response)
            if code is not None:
                _log_api_error("crux", -1, "-", f"http_{code}")
                crux_block = {"status": "unavailable", "reason": _reason_for(_http_error(code))}
            else:
                crux = extract_crux_origin(crux_response)
                crux_block = {"status": "ok", "reason": None}
    psi_failed_urls = 0
    psi_first_reason: str | None = None
    for index, url in enumerate(probed):
        if time.monotonic() >= deadline:
            probed = probed[: max(index, 1)]
            break
        lab: dict[str, float | None] = {"lcp": None, "cls": None, "inp": None}
        field: dict[str, float | None] = {"lcp": None, "cls": None, "inp": None}
        strategies = ("mobile", "desktop") if index == 0 else ("mobile",)
        url_ok = False
        for strategy in strategies:
            if time.monotonic() >= deadline:
                break
            try:
                remaining = max(1.0, min(CALL_TIMEOUT_SECONDS, deadline - time.monotonic()))
                response = _get_json(_psi_url(url, strategy, api_key), remaining)
            except (OSError, ValueError, urllib.error.URLError) as error:
                _log_api_error("psi", index, strategy, type(error).__name__)
                if psi_first_reason is None:
                    psi_first_reason = _reason_for(error)
                continue
            code = _error_code(response)
            if code is not None:
                _log_api_error("psi", index, strategy, f"http_{code}")
                if psi_first_reason is None:
                    psi_first_reason = _reason_for(_http_error(code))
                continue
            url_ok = True
            for metric, value in extract_lab_metrics(response).items():
                if value is not None and (lab[metric] is None or value > lab[metric]):
                    lab[metric] = value
            for metric, value in extract_loading_experience(response).items():
                if field[metric] is None:
                    field[metric] = value
        if not url_ok:
            psi_failed_urls += 1
        if crux is not None:
            for metric, value in crux.items():
                if value is not None:
                    field[metric] = value
        for metric in ("lcp", "cls", "inp"):
            if lab[metric] is None and field[metric] is None:
                continue
            metrics.append({"url": url, "metric": metric, "lab": lab[metric], "field_p75": field[metric]})
    probed_kept = sorted({str(entry["url"]) for entry in metrics}) or probed[:1]
    fetched_any = any(section["http_status"] == 200 for section in sitefiles.values())
    if not metrics and not fetched_any:
        return {"status": "unavailable", "reason": psi_first_reason or "http_503"}
    psi_api_block: dict[str, object] = {"status": "ok", "reason": None}
    if psi_failed_urls:
        psi_api_block = {"status": "degraded", "reason": psi_first_reason or "http_503"}
    return {
        "schema": "extella.psi_source.v1",
        "source": "PSI",
        "site_url": site_url,
        "probed_urls": probed_kept if metrics else probed[:1],
        "metrics": metrics,
        "psi_api": psi_api_block,
        "crux": crux_block,
        "sitefiles": sitefiles,
    }


def _psi_url(url: str, strategy: str, api_key: str) -> str:
    query = {"url": url, "strategy": strategy, "category": "performance"}
    if api_key:
        query["key"] = api_key
    return f"{PSI_API}?{urllib.parse.urlencode(query)}"


def _read_json(raw: bytes) -> object:
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _api_request(url: str, timeout: float, *, payload: dict[str, object] | None = None) -> object:
    """Call a fixed Google API host over a connection pinned to a resolved address."""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in GOOGLE_HOSTS
    ):
        raise ValueError("api target is not allowed")
    port = parsed.port or 443
    deadline = time.monotonic() + timeout
    addresses = _global_addresses(parsed.hostname, port)[:MAX_PINNED_ADDRESSES]
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    headers = {"Host": _host_header(parsed.hostname, parsed.port), "User-Agent": _USER_AGENT}
    method = "GET"
    body = None
    if payload is not None:
        method = "POST"
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    last_error: Exception | None = None
    for address in addresses:
        _remaining(deadline)
        connection = None
        response_obtained = False
        try:
            connection = _PinnedHTTPSConnection(parsed.hostname, port, address, _remaining(deadline))
            connection._deadline = deadline
            _prepare_socket_stage(connection, deadline)
            connection.request(method, path, body=body, headers=headers)
            _prepare_socket_stage(connection, deadline)
            response = connection.getresponse()
            response_obtained = True
            _prepare_socket_stage(connection, deadline)
            if response.status != 200:
                raise urllib.error.HTTPError(url, response.status, "error payload", {}, None)
            raw = _read_response_body(response, connection, 2_000_000, deadline)
            return _read_json(raw)
        except http.client.HTTPException:
            raise
        except urllib.error.HTTPError:
            raise
        except (OSError, TimeoutError) as error:
            if response_obtained:
                raise
            last_error = error
            _remaining(deadline)
        finally:
            if connection is not None:
                connection.close()
    if last_error is not None:
        raise last_error
    raise OSError("api host has no reachable public address")


def _get_json(url: str, timeout: float) -> object:
    return _api_request(url, timeout)


def _post_json(url: str, payload: dict[str, object], timeout: float) -> object:
    return _api_request(url, timeout, payload=payload)


def _request_parts(body: bytes) -> tuple[dict[str, object], int] | None:
    try:
        payload = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("site_url"), str)
        or not isinstance(payload.get("plan"), dict)
        or set(payload["plan"]) != {"max_urls", "timeout_ms"}
        or not isinstance(payload["plan"]["max_urls"], int)
        or isinstance(payload["plan"]["max_urls"], bool)
        or not 1 <= payload["plan"]["max_urls"] <= MAX_URLS
        or not isinstance(payload["plan"]["timeout_ms"], int)
        or isinstance(payload["plan"]["timeout_ms"], bool)
        or not 1 <= payload["plan"]["timeout_ms"] <= MAX_TIMEOUT_MS
    ):
        return None
    return payload, payload["plan"]["timeout_ms"]


def handle_run(body: bytes) -> tuple[int, dict[str, object]]:
    parts = _request_parts(body)
    if parts is None:
        return 400, {"status": "error", "code": "invalid_request"}
    payload, _timeout_ms = parts
    try:
        return 200, run_probe(
            payload["site_url"],
            payload["plan"]["max_urls"],
            payload["plan"]["timeout_ms"],
            os.environ.get("PSI_API_KEY", ""),
        )
    except ValueError:
        return 400, {"status": "error", "code": "invalid_request"}
    except (OSError, urllib.error.URLError) as error:
        return 200, {"status": "unavailable", "reason": _reason_for(error)}


class _ChildEntry:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.pending_reap = False
        self.slot_owned = True


class _PSISupervisor:
    """Own bounded worker children and their slots for the long-lived HTTP parent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: dict[int, _ChildEntry] = {}
        self._slots = threading.BoundedSemaphore(MAX_CHILDREN)
        self._shutdown_requested = threading.Event()
        self._shutdown = False

    def _remove_locked(self, entry: _ChildEntry) -> bool:
        if self._children.get(entry.process.pid) is not entry:
            return False
        del self._children[entry.process.pid]
        if entry.slot_owned:
            entry.slot_owned = False
            self._slots.release()
        return True

    def _reap_exited_locked(self) -> None:
        for pid, entry in list(self._children.items()):
            if entry.process.poll() is None:
                continue
            try:
                entry.process.wait(timeout=0)
            except subprocess.TimeoutExpired:
                continue
            self._remove_locked(entry)

    def spawn(self) -> _ChildEntry | None:
        with self._lock:
            self._reap_exited_locked()
            if self._shutdown or self._shutdown_requested.is_set() or not self._slots.acquire(blocking=False):
                return None
            try:
                process = subprocess.Popen(
                    [sys.executable, os.path.abspath(__file__), CHILD_MODE],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    shell=False,
                    start_new_session=True,
                    close_fds=True,
                )
            except (OSError, ValueError):
                self._slots.release()
                return None
            entry = _ChildEntry(process)
            self._children[process.pid] = entry
            return entry

    def _remove(self, entry: _ChildEntry) -> None:
        with self._lock:
            self._remove_locked(entry)

    def _mark_pending(self, entry: _ChildEntry) -> None:
        with self._lock:
            if self._children.get(entry.process.pid) is entry:
                entry.pending_reap = True

    def finish(self, entry: _ChildEntry) -> bool:
        if entry.process.poll() is None:
            return False
        try:
            entry.process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return False
        self._remove(entry)
        return True

    def kill_and_reap(self, entry: _ChildEntry) -> bool:
        process = entry.process
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                self._mark_pending(entry)
        try:
            process.wait(timeout=CHILD_CLEANUP_SECONDS)
        except subprocess.TimeoutExpired:
            self._mark_pending(entry)
            return False
        self._remove(entry)
        return True

    def health(self) -> _ChildEntry | None:
        with self._lock:
            self._reap_exited_locked()
            for entry in self._children.values():
                if entry.pending_reap:
                    return entry
        return None

    def begin_shutdown(self) -> None:
        self._shutdown_requested.set()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._shutdown_requested.set()
            entries = list(self._children.values())
        for entry in entries:
            self.kill_and_reap(entry)


SUPERVISOR = _PSISupervisor()


def _child_envelope(body: bytes, deadline: float) -> bytes | None:
    parts = _request_parts(body)
    if parts is None:
        return None
    payload, _timeout_ms = parts
    envelope = {
        "site_url": payload["site_url"],
        "plan": payload["plan"],
        "deadline": deadline,
    }
    try:
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return encoded if len(encoded) <= CHILD_IPC_BYTES else None


def _unavailable(reason: str) -> tuple[int, dict[str, object]]:
    return 200, {"status": "unavailable", "reason": reason}


def _exchange_child(entry: _ChildEntry, request: bytes, deadline: float) -> bytes:
    process = entry.process
    if process.stdin is None or process.stdout is None:
        raise OSError("psi child pipes unavailable")
    selector = selectors.DefaultSelector()
    stdin_fd = process.stdin.fileno()
    stdout_fd = process.stdout.fileno()
    os.set_blocking(stdin_fd, False)
    os.set_blocking(stdout_fd, False)
    selector.register(stdin_fd, selectors.EVENT_WRITE, "stdin")
    selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
    offset = 0
    output = bytearray()
    stdin_closed = False
    stdout_closed = False
    try:
        while not stdout_closed or process.poll() is None:
            remaining = _remaining(deadline)
            events = selector.select(remaining)
            if not events:
                _remaining(deadline)
                continue
            for key, mask in events:
                if key.data == "stdin" and mask & selectors.EVENT_WRITE:
                    if offset < len(request):
                        try:
                            written = os.write(stdin_fd, request[offset:])
                        except BlockingIOError:
                            continue
                        if written <= 0:
                            raise OSError("psi child stdin closed")
                        offset += written
                    if offset == len(request) and not stdin_closed:
                        selector.unregister(stdin_fd)
                        process.stdin.close()
                        stdin_closed = True
                if key.data == "stdout" and mask & selectors.EVENT_READ:
                    try:
                        chunk = os.read(stdout_fd, min(8192, CHILD_IPC_BYTES + 1 - len(output)))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stdout_fd)
                        stdout_closed = True
                    else:
                        output.extend(chunk)
                        if len(output) > CHILD_IPC_BYTES:
                            raise ValueError("psi child stdout exceeds cap")
            _remaining(deadline)
        return bytes(output)
    finally:
        selector.close()
        if not stdin_closed:
            process.stdin.close()
        process.stdout.close()


def _decode_child_envelope(raw: bytes) -> tuple[int, dict[str, object]] | None:
    try:
        text = raw.decode("utf-8")
        envelope = json.loads(text)
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, dict) or set(envelope) != {"http_status", "payload"}:
        return None
    status = envelope["http_status"]
    payload = envelope["payload"]
    if isinstance(status, bool) or not isinstance(status, int) or status not in {200, 400}:
        return None
    if not isinstance(payload, dict):
        return None
    return status, payload


def _write_child_output(raw: bytes) -> None:
    fd = sys.stdout.buffer.fileno()
    offset = 0
    while offset < len(raw):
        try:
            written = os.write(fd, raw[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("psi child stdout closed")
        offset += written


def run_isolated(body: bytes) -> tuple[int, dict[str, object]]:
    parts = _request_parts(body)
    if parts is None:
        return 400, {"status": "error", "code": "invalid_request"}
    _payload, timeout_ms = parts
    deadline = time.monotonic() + timeout_ms / 1000
    encoded = _child_envelope(body, deadline)
    if encoded is None:
        return 400, {"status": "error", "code": "invalid_request"}
    try:
        _remaining(deadline)
    except TimeoutError:
        return _unavailable("timeout")
    entry = SUPERVISOR.spawn()
    if entry is None:
        return _unavailable("http_503")
    try:
        try:
            raw = _exchange_child(entry, encoded, deadline)
        except TimeoutError:
            SUPERVISOR.kill_and_reap(entry)
            return _unavailable("timeout")
        except (OSError, ValueError):
            SUPERVISOR.kill_and_reap(entry)
            return _unavailable("http_503")
        if time.monotonic() >= deadline:
            SUPERVISOR.kill_and_reap(entry)
            return _unavailable("timeout")
        decoded = _decode_child_envelope(raw)
        if decoded is None:
            SUPERVISOR.kill_and_reap(entry)
            return _unavailable("http_503")
        status, payload = decoded
        if entry.process.poll() is None:
            SUPERVISOR.kill_and_reap(entry)
            return _unavailable("timeout")
        returncode = entry.process.returncode
        if not SUPERVISOR.finish(entry) or returncode != 0:
            return _unavailable("http_503")
        if time.monotonic() >= deadline:
            return _unavailable("timeout")
        return status, payload
    except BaseException:
        SUPERVISOR.kill_and_reap(entry)
        raise


def _child_main() -> int:
    raw = sys.stdin.buffer.read(CHILD_IPC_BYTES + 1)
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return 2
    if (
        len(raw) > CHILD_IPC_BYTES
        or not isinstance(envelope, dict)
        or set(envelope) != {"site_url", "plan", "deadline"}
        or not isinstance(envelope.get("site_url"), str)
        or not isinstance(envelope.get("plan"), dict)
        or not isinstance(envelope.get("deadline"), (int, float))
        or isinstance(envelope.get("deadline"), bool)
        or not math.isfinite(float(envelope["deadline"]))
    ):
        return 2
    deadline = float(envelope["deadline"])
    if time.monotonic() >= deadline:
        status, payload = _unavailable("timeout")
    else:
        remaining_ms = math.floor((deadline - time.monotonic()) * 1000)
        if remaining_ms < 1:
            status, payload = _unavailable("timeout")
        else:
            body = json.dumps(
                {
                    "site_url": envelope["site_url"],
                    "plan": {"max_urls": envelope["plan"].get("max_urls"), "timeout_ms": remaining_ms},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            status, payload = handle_run(body)
    encoded = json.dumps(
        {"http_status": status, "payload": payload}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > CHILD_IPC_BYTES:
        return 3
    _write_child_output(encoded)
    return 0


class Handler(BaseHTTPRequestHandler):
    server_version = "ExtellaPSI/2.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(HANDLER_IDLE_SECONDS)

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            pending = SUPERVISOR.health()
            if pending is not None:
                self._send(503, {"status": "degraded", "reason": "child_cleanup_pending"})
            else:
                self._send(200, {"status": "ok"})
            return
        self._send(404, {"status": "error", "code": "route_not_found"})

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send(404, {"status": "error", "code": "route_not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            self._send(400, {"status": "error", "code": "invalid_request"})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            self._send(400, {"status": "error", "code": "invalid_request"})
            return
        try:
            body = self.rfile.read(length) if length else b""
        except OSError:
            self.close_connection = True
            self._send(400, {"status": "error", "code": "invalid_request"})
            return
        if len(body) != length:
            self.close_connection = True
            self._send(400, {"status": "error", "code": "invalid_request"})
            return
        try:
            status, payload = run_isolated(body)
        except (OSError, ValueError):
            status, payload = _unavailable("http_503")
        try:
            self._send(status, payload)
        except OSError:
            return

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _send_prethread_unavailable(request: socket.socket) -> None:
    body = b'{"status":"error","code":"http_503"}'
    response = (
        b"HTTP/1.1 503 Service Unavailable\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    try:
        request.settimeout(HANDLER_IDLE_SECONDS)
        request.sendall(response)
    except OSError:
        return
    finally:
        request.close()


class _BoundedPSIHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._handler_slots = threading.BoundedSemaphore(MAX_HANDLERS)

    def process_request(self, request: socket.socket, client_address: object) -> None:
        if not self._handler_slots.acquire(blocking=False):
            _send_prethread_unavailable(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._handler_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: object) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handler_slots.release()


def main() -> int:
    server = _BoundedPSIHTTPServer(("0.0.0.0", PORT), Handler)

    def request_shutdown(_signum: int, _frame: object) -> None:
        SUPERVISOR.begin_shutdown()
        server._BaseServer__shutdown_request = True

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        SUPERVISOR.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == CHILD_MODE:
        raise SystemExit(_child_main())
    raise SystemExit(main())
