#!/usr/bin/env python3
"""Run each current P2 probe worker in an isolated process against a public site."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[3]
WORKER = ROOT / "runtime" / "probe" / "entrypoint.py"
SITE_URL = "https://books.toscrape.com/"
TIMEOUT_MS = 20_000


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _probe(kind: str, port: int) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/run",
        data=json.dumps({"site_url": SITE_URL, "plan": {"timeout_ms": TIMEOUT_MS}}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_MS / 1000 + 5) as response:
            payload = json.loads(response.read(100_000))
        return {
            "kind": kind,
            "transport": "loopback HTTP /run",
            "http_status": response.status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "payload": payload,
        }
    except urllib.error.HTTPError as error:
        return {
            "kind": kind,
            "transport": "loopback HTTP /run",
            "http_status": error.code,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": error.read(100_000).decode("utf-8", errors="replace"),
        }
    except Exception as error:  # evidence records transport failures without hiding them
        return {
            "kind": kind,
            "transport": "loopback HTTP /run",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(error).__name__}: {error}",
        }


def _wait_ready(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        return


def main(output: Path) -> int:
    lanes = [(kind, _free_port()) for kind in ("nu", "tls", "cc")]
    processes: list[tuple[str, int, subprocess.Popen[str]]] = []
    try:
        for kind, port in lanes:
            environment = {**os.environ, "PROBE_KIND": kind, "PORT": str(port)}
            process = subprocess.Popen(
                [sys.executable, str(WORKER)],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            processes.append((kind, port, process))
        # Give fresh interpreters one bounded startup grace period before the one-shot socket checks.
        time.sleep(0.25)
        for _kind, port in lanes:
            _wait_ready(port)
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda item: _probe(item[0], item[1]), lanes))
    finally:
        process_evidence = []
        for kind, port, process in processes:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            process_evidence.append({
                "kind": kind,
                "port": port,
                "returncode": process.returncode,
                "stdout": stdout[-2_000:],
                "stderr": stderr[-2_000:],
            })
    evidence = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "site_url": SITE_URL,
        "timeout_ms": TIMEOUT_MS,
        "worker": str(WORKER.relative_to(ROOT)),
        "isolation": "three fresh subprocesses; no Compose worker reused",
        "results": results,
        "processes": process_evidence,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence["results"], ensure_ascii=False, indent=2))
    return 1 if any("error" in item for item in results) else 0


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) == 2 else ROOT / ".orchestra" / "tasks" / "1" / "isolated-live-workers.json"
    raise SystemExit(main(destination))
