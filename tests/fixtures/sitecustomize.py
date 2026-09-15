"""Test-only provider/DNS fixtures for the external acceptance harness.

The production entrypoint has no test mode.  Python loads this module only
when the harness explicitly supplies PYTHONPATH, and every behavior is gated
on the production child CLI marker so the supervisor parent is untouched.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request


if "--probe-child" in sys.argv:
    mode = os.environ.get("EXTELLA_ACCEPTANCE_CHILD", "")
    fixture_url = os.environ.get("EXTELLA_ACCEPTANCE_FIXTURE_URL", "").rstrip("/")

    if fixture_url:
        original_urlopen = urllib.request.urlopen
        provider_sources = {
            "validator.w3.org": "nu",
            "api.ssllabs.com": "tls",
            "index.commoncrawl.org": "cc",
            "data.commoncrawl.org": "cc",
        }

        def fixture_urlopen(request: object, *args: object, **kwargs: object) -> object:
            original_url = getattr(request, "full_url", request)
            if not isinstance(original_url, str):
                return original_urlopen(request, *args, **kwargs)
            parsed = urllib.parse.urlsplit(original_url)
            source = provider_sources.get(parsed.hostname or "")
            if source is None:
                return original_urlopen(request, *args, **kwargs)
            original_path = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
            local_url = f"{fixture_url}/provider?source={source}&path={urllib.parse.quote(original_path, safe='')}"
            if hasattr(request, "data"):
                data = request.data
                headers = dict(request.header_items()) if hasattr(request, "header_items") else {}
                method = request.get_method() if hasattr(request, "get_method") else None
                replacement = urllib.request.Request(local_url, data=data, headers=headers, method=method)
            else:
                replacement = local_url
            return original_urlopen(replacement, *args, **kwargs)

        urllib.request.urlopen = fixture_urlopen  # type: ignore[assignment]

    if mode == "dns-hang":
        marker = os.environ.get("EXTELLA_ACCEPTANCE_DNS_MARKER", "")

        def hanging_getaddrinfo(*_args: object, **_kwargs: object) -> object:
            if marker:
                with open(marker, "a", encoding="utf-8") as handle:
                    json.dump({"event": "dns_entered", "pid": os.getpid(), "wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}, handle)
                    handle.write("\n")
                    handle.flush()
            threading.Event().wait()
            raise OSError("test-local DNS fixture did not return")

        socket.getaddrinfo = hanging_getaddrinfo  # type: ignore[assignment]
