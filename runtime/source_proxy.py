#!/usr/bin/env python3
"""Call an internal source worker and atomically store its JSON result."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request


ENDPOINTS = {
    "CrawlSEO": os.environ.get("EXTELLA_CRAWLSEO_URL", "http://crawlseo:8081/run"),
    "SEOmator": os.environ.get("EXTELLA_SEOMATOR_URL", "http://seomator:8082/run"),
}
ALLOWED_HOSTS = {"crawlseo", "seomator", "127.0.0.1", "localhost"}
MAX_RESPONSE_BYTES = 10_000_000
WORKER_UNAVAILABLE_REASONS = frozenset({"waf", "captcha", "http_403", "http_429", "http_503", "robots_denied", "timeout"})
SEO_CATEGORIES = frozenset(
    {
        "core", "technical", "perf", "links", "images", "security", "crawl", "schema", "a11y", "content",
        "social", "eeat", "url", "mobile", "i18n", "legal", "js", "redirect", "htmlval", "geo",
    }
)


def _endpoint(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/run"
        or parsed.port is None
    ):
        raise ValueError("source worker endpoint is not allowed")
    return value


def _plan(plan_path: pathlib.Path) -> dict[str, object]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "max_pages", "categories", "performance_sample_pages", "timeout_ms"
    }:
        raise ValueError("source plan is invalid")
    max_pages = payload["max_pages"]
    categories = payload["categories"]
    sample_pages = payload["performance_sample_pages"]
    timeout_ms = payload["timeout_ms"]
    if (
        isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or not 1 <= max_pages <= 100
        or not isinstance(categories, list)
        or not categories
        or any(not isinstance(category, str) or category not in SEO_CATEGORIES for category in categories)
        or len(categories) != len(set(categories))
        or isinstance(sample_pages, bool)
        or not isinstance(sample_pages, int)
        or not 1 <= sample_pages <= min(5, max_pages)
        or isinstance(timeout_ms, bool)
        or not isinstance(timeout_ms, int)
        or not 1 <= timeout_ms <= 720_000
    ):
        raise ValueError("source plan is invalid")
    return payload


def _read_capped(response: object) -> bytes:
    read = getattr(response, "read", None)
    if not callable(read):
        raise ValueError("source worker response is invalid")
    raw = read(MAX_RESPONSE_BYTES + 1)
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("source worker response is too large")
    return raw


def _worker_error_payload(raw: bytes) -> dict[str, str]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"status": "failed", "reason": "audit_failed"}
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        return {"status": "failed", "reason": "audit_failed"}
    if code in WORKER_UNAVAILABLE_REASONS:
        return {"status": "unavailable", "reason": code}
    return {"status": "failed", "reason": "audit_failed"}


def proxy_source(source: str, site_url: str, plan_path: pathlib.Path, output_path: pathlib.Path) -> None:
    endpoint = _endpoint(ENDPOINTS[source])
    plan = _plan(plan_path)
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"site_url": site_url, "plan": plan}, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=min(plan["timeout_ms"] / 1000 + 5, 900)) as response:
            raw = _read_capped(response)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("source worker response is not an object")
    except urllib.error.HTTPError as error:
        payload = _worker_error_payload(_read_capped(error))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=output_path.parent,
            prefix=f".{output_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] not in ENDPOINTS:
        print("usage: source_proxy.py <CrawlSEO|SEOmator> <public-url> <plan-json> <output-json>", file=sys.stderr)
        return 2
    try:
        proxy_source(argv[0], argv[1], pathlib.Path(argv[2]), pathlib.Path(argv[3]))
    except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError):
        print("source worker request failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
