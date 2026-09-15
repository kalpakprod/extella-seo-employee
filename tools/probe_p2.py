#!/usr/bin/env python3
"""Probe running optional Compose workers and save bounded, parsed live evidence."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experts import seo_employee_service as service


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="extella-p2-check")
    parser.add_argument("--url", default="https://books.toscrape.com/")
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "p2-probes-2026-09-15.json")
    args = parser.parse_args()
    plan = service.build_audit_plan("service_b2b", requested_max_pages=25)
    lanes = [("nu", 8085, service.NuHTMLAdapter()), ("tls", 8086, service.SecurityProbeAdapter()), ("cc", 8087, service.CommonCrawlAdapter())]

    def probe(lane):
        kind, port, adapter = lane
        request = json.dumps({"site_url": args.url, "plan": {"timeout_ms": 20000}})
        code = (
            "import urllib.request; "
            f"r=urllib.request.Request('http://127.0.0.1:{port}/run',data={request.encode()!r},headers={{'Content-Type':'application/json'}}); "
            "print(urllib.request.urlopen(r,timeout=25).read(100000).decode())"
        )
        started = time.monotonic()
        raw = subprocess.run(["docker", "compose", "-p", args.project, "-f", str(ROOT / "deploy/compose.yaml"), "exec", "-T", kind, "python", "-c", code], capture_output=True, text=True, timeout=30, check=True)
        payload = json.loads(raw.stdout)
        result = adapter.parse(payload, plan)
        return {"source": adapter.name, "elapsed_seconds": round(time.monotonic() - started, 3),
                "status": result.status, "reason": result.reason, "coverage": result.coverage.as_dict(),
                "findings": [{"rule": item.rule_key, "status": item.status, "fact": item.fact} for item in result.occurrences],
                "payload": payload}

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(probe, lanes))
    evidence = {"checked_at": datetime.now(timezone.utc).isoformat(), "site_url": args.url,
                "timeout_ms": 20000, "transport": "Compose worker HTTP /run", "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps([{k: item[k] for k in ("source", "status", "reason", "elapsed_seconds")} for item in results], indent=2))
    return 1 if any(item["status"] == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
