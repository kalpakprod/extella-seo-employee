#!/usr/bin/env python3
"""Optional P2 probe worker: W3C Nu HTML validation, TLS/security headers, Common Crawl excerpts.

PROBE_KIND selects the lane (nu|tls|cc). Each lane is single-shot, capped, and
stdlib-only; it returns a strict extella.*_source.v1 payload. Thresholds and
verdicts live in the in-product adapters, never here.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import zlib
from html.parser import HTMLParser
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NU_API = "https://validator.w3.org/nu/?out=json"
NU_HOST = "validator.w3.org"
SSL_API = "https://api.ssllabs.com/api/v3/analyze"
SSL_HOST = "api.ssllabs.com"
CC_INDEX_HOST = "index.commoncrawl.org"
CC_DATA_HOST = "data.commoncrawl.org"
HTML_BYTES = 262_144
HEADER_BODY_BYTES = 4_096
EXCERPT_BYTES = 65_536
DECOMPRESSED_BYTES = 262_144
EXCERPT_CHARS = 500
EXEMPLARS = 3
EXEMPLAR_CHARS = 200
CALL_TIMEOUT_SECONDS = 60.0
MAX_BODY_BYTES = 65_536
PORT = int(os.environ.get("PORT", "8085"))
PROBE_KIND = os.environ.get("PROBE_KIND", "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def _is_global_host(hostname: str) -> bool:
    if os.environ.get("PROBE_ALLOW_PRIVATE") == "1":
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    addresses = set()
    for info in infos:
        try:
            addresses.add(ipaddress.ip_address(info[4][0]))
        except ValueError:
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


def _same_origin(left: str, right: str) -> bool:
    first, second = urllib.parse.urlsplit(left), urllib.parse.urlsplit(right)
    return (first.scheme, first.hostname, first.port) == (second.scheme, second.hostname, second.port)


def fetch_bytes(
    url: str, timeout: float, max_bytes: int, *, allowed_hosts: set[str], headers: bool = False
) -> tuple[int, bytes, bool, dict[str, str]]:
    """Fetch with allowlist guard, capped bytes, manual same-origin redirects."""
    if max_bytes < 1 or timeout <= 0:
        raise ValueError("fetch budget is invalid")
    current = url
    opener = urllib.request.build_opener(NoRedirect)
    deadline = time.monotonic() + timeout
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
        request = urllib.request.Request(current, headers={"User-Agent": "ExtellaProbe/2.1"}, method="GET")
        try:
            response = opener.open(request, timeout=_remaining(deadline))
        except urllib.error.HTTPError as error:
            if error.code in {301, 302, 303, 307, 308}:
                location = error.headers.get("Location", "")
                target = urllib.parse.urljoin(current, location)
                if not location or not _same_origin(target, current):
                    raise ValueError("redirect leaves the probed origin") from None
                current = target
                continue
            if 400 <= error.code <= 599:
                return error.code, b"", False, {}
            raise
        with response:
            status = response.status
            seen = {key.lower(): value for key, value in response.getheaders()} if headers else {}
            if status != 200:
                return status, b"", False, seen
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return status, raw[:max_bytes], True, seen
            return status, raw, False, seen
    raise ValueError("too many redirects")


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
    return urllib.error.HTTPError("https://probe.invalid/", code, "error payload", {}, None)


def _log(kind: str, detail: str) -> None:
    sys.stderr.write(f"probe {kind} detail={detail}\n")
    sys.stderr.flush()


def _read_json(raw: bytes) -> object:
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _get_json(url: str, timeout: float, max_bytes: int = 2_000_000) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "ExtellaProbe/2.1"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("JSON response exceeds byte cap")
    # Common Crawl's index returns JSON lines, including multiple captures.
    if urllib.parse.urlsplit(url).hostname == CC_INDEX_HOST and "-index?" in url:
        return [_read_json(line) for line in raw.splitlines() if line.strip()]
    return _read_json(raw)


def _remaining(deadline: float) -> float:
    remaining = min(CALL_TIMEOUT_SECONDS, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError("probe deadline exceeded")
    return remaining


def _check_site_host(site_url: str) -> tuple[str, set[str]]:
    parsed = urllib.parse.urlsplit(site_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("site url is invalid")
    if not _is_global_host(parsed.hostname):
        raise ValueError("site host is not a global address")
    return f"{parsed.scheme}://{parsed.netloc}", {parsed.hostname}


def _nu_messages(payload: object) -> tuple[int, int, list[dict[str, object]]]:
    errors = 0
    warnings = 0
    exemplars: list[dict[str, object]] = []
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        raise ValueError("validator response is invalid")
    for message in messages:
        if isinstance(message, dict) and message.get("type") == "non-document-error":
            raise ValueError("validator could not validate the document")
        if not isinstance(message, dict):
            continue
        kind = message.get("type")
        if kind == "error":
            errors += 1
        elif kind == "info" and message.get("subType") == "warning":
            warnings += 1
        else:
            continue
        if len(exemplars) < EXEMPLARS:
            text = message.get("message")
            line = message.get("lastLine")
            exemplars.append({
                "kind": "error" if kind == "error" else "warning",
                "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
                "message": str(text)[:EXEMPLAR_CHARS] if isinstance(text, str) else "",
            })
    return errors, warnings, exemplars


def run_nu_probe(site_url: str, timeout_ms: int) -> dict[str, object]:
    deadline = time.monotonic() + timeout_ms / 1000
    _origin, allowed = _check_site_host(site_url)
    try:
        status, body, truncated, _seen = fetch_bytes(
            site_url, _remaining(deadline), HTML_BYTES, allowed_hosts=allowed,
        )
    except (OSError, ValueError, urllib.error.URLError) as error:
        _log("nu", f"fetch {type(error).__name__}")
        return {"status": "unavailable", "reason": _reason_for(error)}
    if status != 200:
        return {"status": "unavailable", "reason": _reason_for(_http_error(status))}
    if truncated:
        return {"status": "unavailable", "reason": "http_503"}
    request = urllib.request.Request(
        NU_API,
        data=body,
        headers={"Content-Type": "text/html; charset=utf-8", "User-Agent": "ExtellaProbe/2.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_remaining(deadline)) as response:
            raw = response.read(2_000_000 + 1)
            if len(raw) > 2_000_000:
                raise ValueError("validator response exceeds cap")
            payload = _read_json(raw)
    except (OSError, ValueError, urllib.error.URLError) as error:
        _log("nu", f"validator {type(error).__name__}")
        return {"status": "unavailable", "reason": _reason_for(error)}
    try:
        errors, warnings, exemplars = _nu_messages(payload)
    except ValueError:
        return {"status": "unavailable", "reason": "http_503"}
    return {
        "schema": "extella.nu_source.v1",
        "source": "NuHTML",
        "site_url": site_url,
        "probed_urls": [site_url],
        "fetch": {"http_status": status, "truncated": truncated, "bytes": len(body)},
        "errors": errors,
        "warnings": warnings,
        "exemplars": exemplars,
    }


_SSL_GRADES = ("A+", "A", "A-", "B", "C", "D", "E", "F", "T", "M")


def _hsts_max_age(value: str | None) -> int | None:
    if not value:
        return None
    for part in value.split(";"):
        name, _, number = part.strip().partition("=")
        if name.strip().lower() != "max-age":
            continue
        try:
            age = int(number.strip())
        except ValueError:
            return None
        return age if age >= 0 else None
    return None


def _ssl_grade(payload: object) -> tuple[str, str | None]:
    """Single cached-assessment read; never polls, never starts a scan we wait on."""
    if not isinstance(payload, dict):
        return "unavailable", None
    status = payload.get("status")
    if status != "READY":
        return (str(status).lower() if isinstance(status, str) and status else "unavailable"), None
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        return "ready", None
    worst: str | None = None
    for endpoint in endpoints:
        grade = endpoint.get("grade") if isinstance(endpoint, dict) else None
        if not isinstance(grade, str) or grade not in _SSL_GRADES:
            continue
        if worst is None or _SSL_GRADES.index(grade) > _SSL_GRADES.index(worst):
            worst = grade
    return "ready", worst


def run_tls_probe(site_url: str, timeout_ms: int) -> dict[str, object]:
    deadline = time.monotonic() + timeout_ms / 1000
    _origin, allowed = _check_site_host(site_url)
    host = urllib.parse.urlsplit(site_url).hostname or ""
    try:
        status, _body, _truncated, seen = fetch_bytes(
            site_url, _remaining(deadline), HEADER_BODY_BYTES, allowed_hosts=allowed, headers=True,
        )
    except (OSError, ValueError, urllib.error.URLError) as error:
        _log("tls", f"fetch {type(error).__name__}")
        return {"status": "unavailable", "reason": _reason_for(error)}
    if status != 200:
        return {"status": "unavailable", "reason": _reason_for(_http_error(status))}
    hsts = seen.get("strict-transport-security")
    csp = seen.get("content-security-policy")
    headers = {
        "hsts": hsts is not None,
        "hsts_max_age": _hsts_max_age(hsts),
        "csp": bool(csp and csp.strip()),
        "x_content_type_options": seen.get("x-content-type-options", "").lower() == "nosniff",
        "referrer_policy": seen.get("referrer-policy", "") != "",
        "frame_guard": seen.get("x-frame-options", "") != "" or "frame-ancestors" in (csp or "").lower(),
    }
    labs_query = urllib.parse.urlencode({"host": host, "fromCache": "on", "maxAge": 24})
    try:
        labs = _get_json(f"{SSL_API}?{labs_query}", _remaining(deadline))
    except (OSError, ValueError, urllib.error.URLError) as error:
        _log("tls", f"labs {type(error).__name__}")
        labs_block: dict[str, object] = {"status": "unavailable", "detail": _reason_for(error), "grade": None}
    else:
        labs_status, grade = _ssl_grade(labs)
        if labs_status == "ready":
            labs_block = {"status": "ready", "detail": None, "grade": grade}
        elif labs_status == "unavailable":
            labs_block = {"status": "unavailable", "detail": None, "grade": None}
        else:
            labs_block = {"status": "pending", "detail": labs_status, "grade": None}
    return {
        "schema": "extella.tls_source.v1",
        "source": "SecurityProbe",
        "site_url": site_url,
        "probed_urls": [site_url],
        "fetch": {"http_status": status},
        "headers": headers,
        "ssl_labs": labs_block,
    }


def _cc_index_id(collinfo: object) -> str | None:
    if not isinstance(collinfo, list) or not collinfo:
        return None
    first = collinfo[0]
    if not isinstance(first, dict):
        return None
    value = first.get("id")
    return value if isinstance(value, str) and re.fullmatch(r"CC-MAIN-[0-9]{4}-[0-9]{2}", value) else None


def _cc_record(payload: object) -> dict[str, object] | None:
    if isinstance(payload, dict):
        candidate: object = payload
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        candidate = payload[0]
    else:
        return None
    assert isinstance(candidate, dict)
    url = candidate.get("url")
    filename = candidate.get("filename")
    offset = candidate.get("offset")
    length = candidate.get("length")
    status = candidate.get("status")
    if (
        not isinstance(url, str)
        or not isinstance(filename, str)
        or not re.fullmatch(r"crawl-data/[a-zA-Z0-9_./-]+\.warc\.gz", filename)
        or ".." in filename.split("/")
        or not isinstance(offset, str)
        or not offset.isdigit()
        or not isinstance(length, str)
        or not length.isdigit()
        or status != "200"
    ):
        return None
    try:
        start = int(offset)
        size = int(length)
    except ValueError:
        return None
    if start < 0 or size <= 0:
        return None
    return {"url": url, "filename": filename, "offset": start, "length": size, "status": status}


class _ExcerptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _archive_excerpt(raw: bytes) -> str:
    try:
        expanded = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, DECOMPRESSED_BYTES)
    except zlib.error as error:
        raise ValueError("invalid compressed archive record") from error
    warc, separator, http = expanded.partition(b"\r\n\r\n")
    headers, boundary, body = http.partition(b"\r\n\r\n")
    if (not warc.startswith(b"WARC/") or not separator or not boundary
            or not headers.startswith(b"HTTP/") or b"text/html" not in headers.lower()):
        return ""
    # Compressed/chunked inner HTTP bodies need a separate decoder; never quote binary.
    header_names = {line.partition(b":")[0].strip().lower() for line in headers.split(b"\r\n")[1:]}
    if b"content-encoding" in header_names or b"transfer-encoding" in header_names:
        return ""
    parser = _ExcerptParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    return " ".join(" ".join(parser.parts).split())[:EXCERPT_CHARS]


def run_cc_probe(site_url: str, timeout_ms: int) -> dict[str, object]:
    deadline = time.monotonic() + timeout_ms / 1000
    parsed = urllib.parse.urlsplit(site_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("site url is invalid")
    try:
        collinfo = _get_json(f"https://{CC_INDEX_HOST}/collinfo.json", _remaining(deadline))
        index_id = _cc_index_id(collinfo)
        if index_id is None:
            return {"status": "unavailable", "reason": "http_503"}
        lookup = urllib.parse.urlencode({"url": site_url, "output": "json", "filter": "status:200", "limit": 1, "matchType": "exact"})
        try:
            index_payload = _get_json(f"https://{CC_INDEX_HOST}/{index_id}-index?{lookup}", _remaining(deadline))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            index_payload = []
        record = _cc_record(index_payload)
        if record is not None and urllib.parse.urlsplit(str(record["url"])).hostname != parsed.hostname:
            raise ValueError("archive record is for another host")
        if record is None:
            _log("cc", "no record")
            return {
                "schema": "extella.cc_source.v1",
                "source": "CommonCrawl",
                "site_url": site_url,
                "probed_urls": [site_url],
                "index": index_id,
                "record": None,
                "excerpt": "",
            }
        assert isinstance(record["offset"], int)
        end = record["offset"] + min(EXCERPT_BYTES, int(record["length"])) - 1
        request = urllib.request.Request(
            f"https://{CC_DATA_HOST}/{record['filename']}",
            headers={"User-Agent": "ExtellaProbe/2.1", "Range": f"bytes={record['offset']}-{end}"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=_remaining(deadline)) as response:
            expected_range = f"bytes {record['offset']}-"
            if response.status != 206 or not response.headers.get("Content-Range", "").startswith(expected_range):
                raise ValueError("archive server ignored the byte range")
            raw = response.read(min(EXCERPT_BYTES, int(record["length"])))
        excerpt = _archive_excerpt(raw)
    except (OSError, ValueError, urllib.error.URLError) as error:
        _log("cc", type(error).__name__)
        return {"status": "unavailable", "reason": _reason_for(error)}
    return {
        "schema": "extella.cc_source.v1",
        "source": "CommonCrawl",
        "site_url": site_url,
        "probed_urls": [site_url],
        "index": index_id,
        "record": record,
        "excerpt": excerpt,
    }


_RUNNERS = {"nu": run_nu_probe, "tls": run_tls_probe, "cc": run_cc_probe}


def handle_run(body: bytes, kind: str) -> tuple[int, dict[str, object]]:
    runner = _RUNNERS.get(kind)
    if runner is None:
        return 400, {"status": "error", "code": "unknown_probe_kind"}
    try:
        payload = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 400, {"status": "error", "code": "invalid_request"}
    plan = payload.get("plan") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("site_url"), str)
        or not isinstance(plan, dict)
        or set(plan) != {"timeout_ms"}
        or not isinstance(plan["timeout_ms"], int)
        or isinstance(plan["timeout_ms"], bool)
        or not 1 <= plan["timeout_ms"] <= 720_000
    ):
        return 400, {"status": "error", "code": "invalid_request"}
    try:
        return 200, runner(payload["site_url"], plan["timeout_ms"])
    except ValueError:
        return 400, {"status": "error", "code": "invalid_request"}
    except (OSError, ValueError, urllib.error.URLError) as error:
        return 200, {"status": "unavailable", "reason": _reason_for(error)}


class Handler(BaseHTTPRequestHandler):
    server_version = "ExtellaProbe/2.1"

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
            self._send(200, {"status": "ok", "kind": PROBE_KIND or None})
            return
        self._send(404, {"status": "error", "code": "route_not_found"})

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send(404, {"status": "error", "code": "route_not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, {"status": "error", "code": "invalid_request"})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send(400, {"status": "error", "code": "invalid_request"})
            return
        status, payload = handle_run(self.rfile.read(length) if length else b"", PROBE_KIND)
        self._send(status, payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    if PROBE_KIND not in _RUNNERS:
        raise SystemExit("PROBE_KIND must be one of nu, tls, cc")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
