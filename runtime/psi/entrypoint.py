#!/usr/bin/env python3
"""PageSpeed/CrUX + site-files probe worker.

This is the only egress point of the PSI lane: it fetches Google PageSpeed and
CrUX data plus bounded site files (robots.txt, sitemap.xml, homepage HTML) and
returns a strict extella.psi_source.v1 payload. Thresholds and verdicts live in
the in-product PSIAdapter, never here.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PSI_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_API = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
GOOGLE_HOSTS = {"www.googleapis.com", "chromeuxreport.googleapis.com"}
MAX_URLS = 3
FETCH_BYTES = {"robots_txt": 65_536, "sitemap_xml": 65_536, "homepage_html": 262_144}
CALL_TIMEOUT_SECONDS = 60.0
MAX_BODY_BYTES = 65_536
PORT = int(os.environ.get("PORT", "8084"))

_LCP_KEYS = ("largest-contentful-paint",)
_INP_KEYS = ("interaction-to-next-paint",)
_CLS_KEYS = ("cumulative-layout-shift",)
_FIELD_KEYS = {
    "lcp": "LARGEST_CONTENTFUL_PAINT_MS",
    "inp": "INTERACTION_TO_NEXT_PAINT",
    "cls": "CUMULATIVE_LAYOUT_SHIFT",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def _is_global_host(hostname: str) -> bool:
    if os.environ.get("PSI_ALLOW_PRIVATE") == "1":
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


def fetch_bytes(url: str, timeout: float, max_bytes: int, *, allowed_hosts: set[str]) -> tuple[int, bytes, bool]:
    """Fetch with same-origin-or-allowlist guard, capped bytes, manual redirects."""
    if max_bytes < 1 or timeout <= 0:
        raise ValueError("fetch budget is invalid")
    current = url
    opener = urllib.request.build_opener(NoRedirect)
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
        if parsed.hostname not in GOOGLE_HOSTS and not _is_global_host(parsed.hostname):
            raise ValueError("fetch target is not a global address")
        request = urllib.request.Request(current, headers={"User-Agent": "ExtellaPSI/2.1"}, method="GET")
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code in {301, 302, 303, 307, 308}:
                location = error.headers.get("Location", "")
                target = urllib.parse.urljoin(current, location)
                if not location or not _same_origin(target, current):
                    raise ValueError("redirect leaves the probed origin") from None
                current = target
                continue
            if 400 <= error.code <= 599:
                return error.code, b"", False
            raise
        with response:
            status = response.status
            if status != 200:
                return status, b"", False
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return status, raw[:max_bytes], True
            return status, raw, False
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


def _get_json(url: str, timeout: float) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "ExtellaPSI/2.1"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _read_json(response.read(2_000_000 + 1))


def _post_json(url: str, payload: dict[str, object], timeout: float) -> object:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "ExtellaPSI/2.1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _read_json(response.read(2_000_000 + 1))


def handle_run(body: bytes) -> tuple[int, dict[str, object]]:
    try:
        payload = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 400, {"status": "error", "code": "invalid_request"}
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
        or not 1 <= payload["plan"]["timeout_ms"] <= 720_000
    ):
        return 400, {"status": "error", "code": "invalid_request"}
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


class Handler(BaseHTTPRequestHandler):
    server_version = "ExtellaPSI/2.1"

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
            self._send(400, {"status": "error", "code": "invalid_request"})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send(400, {"status": "error", "code": "invalid_request"})
            return
        status, payload = handle_run(self.rfile.read(length) if length else b"")
        self._send(status, payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
