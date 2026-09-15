"""P2 integration: source collection reaches the real probe HTTP handlers.

The three provider-facing calls are patched at the probe boundary.  The service,
subprocess source proxy, loopback HTTP transport, probe handlers, adapters, and
report builder remain real so this covers the user-visible collection path without
calling paid or public providers during CI.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import socket
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from experts import seo_employee_service as service


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("p2_probe_http_worker", ROOT / "runtime" / "probe" / "entrypoint.py")
assert SPEC is not None and SPEC.loader is not None
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)


class _Response:
    status = 206
    headers = {"Content-Range": "bytes 0-999/1000"}

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self._body


def _lane_handler(kind: str):
    """Bind a real handler instance to one lane without changing production code."""
    class LaneHandler(WORKER.Handler):
        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            original = WORKER.PROBE_KIND
            WORKER.PROBE_KIND = kind
            try:
                super().do_POST()
            finally:
                WORKER.PROBE_KIND = original
    return LaneHandler


class P2ProbeCollectionIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.servers: list[ThreadingHTTPServer] = []
        self.threads: list[threading.Thread] = []
        self.ports = {kind: self._start(kind) for kind in ("nu", "tls", "cc")}

    def tearDown(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(1)

    def _start(self, kind: str) -> int:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _lane_handler(kind))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append(server)
        self.threads.append(thread)
        return server.server_port

    def test_collect_sources_through_http_probes_builds_an_honest_report(self) -> None:
        plan = service.build_audit_plan("service_b2b", requested_max_pages=1)
        archive = gzip.compress(
            b"WARC/1.0\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
            b"<title>Archived example</title><p>Historical copy</p>"
        )

        def fetch(site_url: str, _timeout: float, _limit: int, *, headers: bool = False, **_kwargs: object):
            self.assertEqual(site_url, "https://example.com/")
            return (200, b"<html><head></head><body>example</body></html>", False,
                    {"strict-transport-security": "max-age=0"} if headers else {})

        def get_json(url: str, _timeout: float, _limit: int = 2_000_000):
            if "ssllabs.com" in url:
                return {"status": "READY", "endpoints": [{"grade": "C"}]}
            if url.endswith("/collinfo.json"):
                return [{"id": "CC-MAIN-2026-33"}]
            if "-index?" in url:
                return {"url": "https://example.com/", "filename": "crawl-data/example.warc.gz", "offset": "0", "length": "999", "status": "200"}
            raise AssertionError(f"unexpected provider URL: {url}")

        def urlopen(request: object, **_kwargs: object) -> _Response:
            url = request.full_url
            if "validator.w3.org" in url:
                return _Response(json.dumps({"messages": [{"type": "error", "lastLine": 4, "message": "missing lang"}]}).encode())
            if "data.commoncrawl.org" in url:
                return _Response(archive)
            raise AssertionError(f"unexpected provider request: {url}")

        endpoints = {
            "EXTELLA_NU_URL": f"http://127.0.0.1:{self.ports['nu']}/run",
            "EXTELLA_TLS_URL": f"http://127.0.0.1:{self.ports['tls']}/run",
            "EXTELLA_CC_URL": f"http://127.0.0.1:{self.ports['cc']}/run",
        }
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(WORKER, "_is_global_host", return_value=True), \
             mock.patch.object(WORKER, "fetch_bytes", side_effect=fetch), \
             mock.patch.object(WORKER, "_get_json", side_effect=get_json), \
             mock.patch.object(WORKER.urllib.request, "urlopen", side_effect=urlopen), \
             mock.patch.dict(os.environ, endpoints):
            root = Path(directory)
            executables = self._make_probe_wrappers(root)

            def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
                name = Path(argv[0]).name
                if name in executables:
                    completed = subprocess.run(argv, **kwargs)
                    self.assertEqual(completed.returncode, 0)
                    return completed
                if name == "run_psi":
                    return SimpleNamespace(returncode=1)
                source = "CrawlSEO" if name == "run_crawlseo" else "SEOmator"
                Path(argv[3]).write_text(json.dumps(self._required_payload(source, plan)), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            original = service.PROBE_EXECUTABLES
            service.PROBE_EXECUTABLES = {
                "NuHTML": executables["run_nu"],
                "SecurityProbe": executables["run_tls"],
                "CommonCrawl": executables["run_cc"],
            }
            try:
                statuses, results = service.collect_sources(
                    "https://example.com/", "http-probe-run", plan=plan,
                    evidence_dir=root / "evidence", runner=runner,
                    crawlseo_executable=root / "run_crawlseo", seomator_executable=root / "run_seomator",
                    psi_executable=root / "run_psi", obtained_at="2026-09-15T00:00:00Z",
                )
            finally:
                service.PROBE_EXECUTABLES = original

        self.assertEqual([item["name"] for item in statuses[:6]], ["CrawlSEO", "SEOmator", "PSI", "NuHTML", "SecurityProbe", "CommonCrawl"])
        self.assertEqual({name: result.status for name, result in results.items()}, {
            "CrawlSEO": "ok", "SEOmator": "ok", "PSI": "unavailable", "NuHTML": "ok", "SecurityProbe": "ok", "CommonCrawl": "ok",
        })
        report, _baseline = service._build_v2_report(
            target={"target_id": "target-example"}, plan=plan,
            command={"site_url": "https://example.com/"}, run_id="http-probe-run",
            started_at="2026-09-15T00:00:00Z", completed_at="2026-09-15T00:00:01Z",
            results=results, baseline=None, enricher=lambda _item: (_ for _ in ()).throw(OSError("offline")),
        )
        self.assertEqual(report["state"], "ready")
        self.assertIn("PSI", report["missing_data"])
        self.assertNotIn("NuHTML", report["missing_data"])
        self.assertNotIn("SecurityProbe", report["missing_data"])
        self.assertEqual({item.source_rule for item in results["NuHTML"].occurrences}, {"NU_ERRORS"})
        self.assertEqual({item.source_rule for item in results["SecurityProbe"].occurrences}, {"TLS_HSTS", "TLS_CSP", "TLS_GRADE"})
        self.assertEqual(results["CommonCrawl"].coverage.crawled_pages, 0)
        self.assertTrue(any("historical" in note for note in results["CommonCrawl"].coverage.notes))

    def _make_probe_wrappers(self, root: Path) -> dict[str, Path]:
        wrappers: dict[str, Path] = {}
        for name, source in (("run_nu", "NuHTML"), ("run_tls", "SecurityProbe"), ("run_cc", "CommonCrawl")):
            path = root / name
            path.write_text(f"#!/bin/sh\nexec python3 {ROOT / 'runtime' / 'source_proxy.py'} {source} \"$@\"\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            wrappers[name] = path
        return wrappers

    @staticmethod
    def _required_payload(source: str, plan: object) -> dict[str, object]:
        categories = list(plan.categories)
        coverage = {"planned_pages": 1, "crawled_pages": 1, "sampled_pages": 1 if source == "SEOmator" else 0, "categories": categories}
        if source == "CrawlSEO":
            return {"schema": "extella.crawlseo_source.v1", "source": source, "tool": "run_crawl", "tool_calls": 1, "requested_max_pages": 1, "crawl": {"status": "COMPLETED", "maxPages": 1, "pagesFound": 1}, "coverage": coverage, "issues": []}
        return {"url": "https://example.com/", "crawledPages": 1, "coverage": coverage, "categoryResults": [{"categoryId": category, "results": []} for category in categories]}


if __name__ == "__main__":
    unittest.main()
