#!/usr/bin/env python3
"""Call the loopback product API without printing its token."""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request


TOKEN_FILE = pathlib.Path(__file__).with_name("secrets") / "seo_employee_api_token"
RUN_DEADLINE_SECONDS = 180.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("health", "state", "preflight", "configure", "run"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--target-id", default="")
    parser.add_argument("--target-name", default="")
    parser.add_argument("--site-url", default="")
    parser.add_argument("--profile", default="service_b2b")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--region", default="GLOBAL")
    parser.add_argument("--site-type", default="website")
    parser.add_argument("--business-goal", default="organic_visibility")
    parser.add_argument("--daily-run-time", default="")
    parser.add_argument("--timezone", default="")
    parser.add_argument("--max-pages", type=int, default=25)
    parser.add_argument("--mode", default="")
    parser.add_argument("--ownership-confirmed", action="store_true")
    args = parser.parse_args()

    origin = urllib.parse.urlsplit(args.base_url)
    if (
        origin.scheme != "http"
        or origin.hostname not in {"127.0.0.1", "localhost"}
        or origin.port is None
        or origin.path not in {"", "/"}
        or origin.username is not None
        or origin.password is not None
        or origin.query
        or origin.fragment
    ):
        raise SystemExit("base URL must be an explicit loopback HTTP origin")

    state_path = "/api/state" + (
        "?" + urllib.parse.urlencode({"target_id": args.target_id}) if args.target_id else ""
    )
    routes = {
        "health": ("GET", "/health", {}),
        "state": ("GET", state_path, {}),
        "preflight": ("POST", "/api/preflight", {}),
        "configure": (
            "POST",
            "/api/configure",
            {
                "target_name": args.target_name,
                "site_url": args.site_url,
                "profile": args.profile,
                "language": args.language,
                "region": args.region,
                "site_type": args.site_type,
                "business_goal": args.business_goal,
                "daily_run_time": args.daily_run_time,
                "timezone": args.timezone,
                "max_pages": args.max_pages,
                "mode": args.mode,
                "ownership_confirmed": args.ownership_confirmed,
            },
        ),
        "run": ("POST", "/api/run", {"target_id": args.target_id, "mode": args.mode}),
    }
    method, path, payload = routes[args.action]
    headers = {"Content-Type": "application/json"}
    if args.action != "health":
        headers["Authorization"] = f"Bearer {TOKEN_FILE.read_text(encoding='utf-8').strip()}"
    request = urllib.request.Request(
        args.base_url.rstrip("/") + path,
        data=json.dumps(payload).encode() if method == "POST" else None,
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=RUN_DEADLINE_SECONDS)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        print(json.dumps(json.loads(response.read()), ensure_ascii=False, indent=2))
        return 0 if response.status < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
