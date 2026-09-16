"""P2 integration: collection reaches isolated probe children and an honest report.

The service, source proxy subprocesses, loopback HTTP transport, probe supervisors and
children, adapters, and report builder remain real. A temporary child-only sitecustomize
redirects the fixed public provider URLs to a local fixture, so CI never calls paid or
public providers while preserving the production process boundary.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from experts import seo_employee_service as service


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("p2_probe_http_worker", ROOT / "runtime" / "probe" / "entrypoint.py")
assert SPEC is not None and SPEC.loader is not None
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)


class _ProviderHandler(BaseHTTPRequestHandler):
    archive = gzip.compress(
        b"WARC/1.0\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
        b"<title>Archived example</title><p>Historical copy</p>"
    )

    def _reply(self, status: int, body: bytes, content_type: str = "application/json", extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        if status == 206:
            self.send_header("Content-Range", f"bytes 0-{len(body) - 1}/{len(body)}")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/validator":
            self._reply(200, json.dumps({"messages": [{"type": "error", "lastLine": 4, "message": "missing lang"}]}).encode())
            return
        self._reply(404, b"{}")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = WORKER.urllib.parse.urlsplit(self.path)
        if parsed.path == "/site":
            self._reply(200, b"<html><head></head><body>example</body></html>", "text/html", {"Strict-Transport-Security": "max-age=0"})
            return
        if parsed.path == "/ssl":
            self._reply(200, json.dumps({"status": "READY", "endpoints": [{"grade": "C"}]}).encode())
            return
        if parsed.path == "/collinfo.json":
            self._reply(200, b'[{"id":"CC-MAIN-2026-33"}]')
            return
        if "-index" in parsed.path:
            record = {"url": self.server.site_url, "filename": "crawl-data/example.warc.gz", "offset": "0", "length": str(len(self.archive)), "status": "200"}
            self._reply(200, json.dumps(record).encode())
            return
        if parsed.path.startswith("/data/"):
            self._reply(206, self.archive, "application/octet-stream")
            return
        self._reply(404, b"{}")

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _ProviderServer(ThreadingHTTPServer):
    site_url: str


class _TlsSiteHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = b"<html><head></head><body>example</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Strict-Transport-Security", "max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _TlsSiteServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], context: ssl.SSLContext) -> None:
        self.context = context
        super().__init__(address, _TlsSiteHandler)

    def get_request(self):
        raw, address = super().get_request()
        return self.context.wrap_socket(raw, server_side=True), address


def _lane_handler(kind: str):
    """Bind a real production handler to one lane without altering its supervisor."""
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
        WORKER.SUPERVISOR = WORKER._ProbeSupervisor()
        self.servers: list[ThreadingHTTPServer] = []
        self.threads: list[threading.Thread] = []
        self.provider = _ProviderServer(("127.0.0.1", 0), _ProviderHandler)
        provider_thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        provider_thread.start()
        self.servers.append(self.provider)
        self.threads.append(provider_thread)
        self.cert_dir = tempfile.TemporaryDirectory(prefix="extella-p2-integration-cert-")
        cert = Path(self.cert_dir.name) / "cert.pem"
        self.cert = cert
        key = Path(self.cert_dir.name) / "key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=example.com", "-addext", "subjectAltName=DNS:example.com",
                "-keyout", str(key), "-out", str(cert),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.site = _TlsSiteServer(("127.0.0.1", 0), context)
        self.site_url = "https://example.com/"
        self.provider.site_url = self.site_url
        site_thread = threading.Thread(target=self.site.serve_forever, daemon=True)
        site_thread.start()
        self.servers.append(self.site)
        self.threads.append(site_thread)
        self.ports = {kind: self._start(kind) for kind in ("nu", "tls", "cc")}
        for port in (self.provider.server_port, self.site.server_port, *self.ports.values()):
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                pass

    def tearDown(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(1)
        self.cert_dir.cleanup()

    def _start(self, kind: str) -> int:
        server = WORKER._BoundedProbeHTTPServer(("127.0.0.1", 0), _lane_handler(kind))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append(server)
        self.threads.append(thread)
        return server.server_port

    def test_collect_sources_through_http_probes_builds_an_honest_report(self) -> None:
        plan = service.build_audit_plan("service_b2b", requested_max_pages=1)

        endpoints = {
            "EXTELLA_NU_URL": f"http://127.0.0.1:{self.ports['nu']}/run",
            "EXTELLA_TLS_URL": f"http://127.0.0.1:{self.ports['tls']}/run",
            "EXTELLA_CC_URL": f"http://127.0.0.1:{self.ports['cc']}/run",
            "PROBE_ALLOW_PRIVATE": "1",
            "PROBE_FIXTURE_BASE": f"http://127.0.0.1:{self.provider.server_port}",
            "PROBE_SITE_PORT": str(self.site.server_port),
            "SSL_CERT_FILE": str(self.cert),
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, endpoints):
            root = Path(directory)
            Path(directory, "sitecustomize.py").write_text(
                "import os, socket, sys, urllib.parse, urllib.request\n\n"
                "if '--probe-child' in sys.argv:\n"
                "    _original_urlopen = urllib.request.urlopen\n"
                "    _original_create_connection = socket.create_connection\n"
                "    _base = os.environ['PROBE_FIXTURE_BASE']\n"
                "    _site_port = int(os.environ['PROBE_SITE_PORT'])\n"
                "    def create_connection(address, timeout=None, *args, **kwargs):\n"
                "        if address[1] == 443:\n"
                "            address = ('127.0.0.1', _site_port)\n"
                "        return _original_create_connection(address, timeout, *args, **kwargs)\n"
                "    def _redirect(request):\n"
                "        url = request.full_url if hasattr(request, 'full_url') else str(request)\n"
                "        parsed = urllib.parse.urlsplit(url)\n"
                "        if parsed.netloc == 'validator.w3.org':\n"
                "            return _base + '/validator'\n"
                "        if parsed.netloc == 'api.ssllabs.com':\n"
                "            return _base + '/ssl?' + parsed.query\n"
                "        if parsed.netloc == 'index.commoncrawl.org':\n"
                "            return _base + parsed.path + ('?' + parsed.query if parsed.query else '')\n"
                "        if parsed.netloc == 'data.commoncrawl.org':\n"
                "            return _base + '/data' + parsed.path\n"
                "        return None\n"
                "    def urlopen(request, *args, **kwargs):\n"
                "        target = _redirect(request)\n"
                "        if target is None:\n"
                "            return _original_urlopen(request, *args, **kwargs)\n"
                "        if hasattr(request, 'full_url'):\n"
                "            request = urllib.request.Request(target, data=request.data, headers=dict(request.header_items()), method=request.get_method())\n"
                "        else:\n"
                "            request = target\n"
                "        return _original_urlopen(request, *args, **kwargs)\n"
                "    urllib.request.urlopen = urlopen\n"
                "    socket.create_connection = create_connection\n",
                encoding="utf-8",
            )
            old_pythonpath = os.environ.get("PYTHONPATH", "")
            os.environ["PYTHONPATH"] = directory + (os.pathsep + old_pythonpath if old_pythonpath else "")
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
                Path(argv[3]).write_text(json.dumps(self._required_payload(source, plan, self.provider.site_url)), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            original = service.PROBE_EXECUTABLES
            service.PROBE_EXECUTABLES = {
                "NuHTML": executables["run_nu"],
                "SecurityProbe": executables["run_tls"],
                "CommonCrawl": executables["run_cc"],
            }
            try:
                statuses, results = service.collect_sources(
                    self.provider.site_url, "http-probe-run", plan=plan,
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
            command={"site_url": self.provider.site_url}, run_id="http-probe-run",
            started_at="2026-09-15T00:00:00Z", completed_at="2026-09-15T00:00:01Z",
            results=results, baseline=None, enricher=lambda _item: (_ for _ in ()).throw(OSError("offline")),
        )
        self.assertEqual(report["state"], "ready")
        self.assertEqual(set(report["missing_data"]), {"PSI", "GoogleSearchConsole", "DataForSEO"})
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
    def _required_payload(source: str, plan: object, site_url: str) -> dict[str, object]:
        categories = list(plan.categories)
        coverage = {"planned_pages": 1, "crawled_pages": 1, "sampled_pages": 1 if source == "SEOmator" else 0, "categories": categories}
        if source == "CrawlSEO":
            return {"schema": "extella.crawlseo_source.v1", "source": source, "tool": "run_crawl", "tool_calls": 1, "requested_max_pages": 1, "crawl": {"status": "COMPLETED", "maxPages": 1, "pagesFound": 1}, "coverage": coverage, "issues": []}
        return {"url": site_url, "crawledPages": 1, "coverage": coverage, "categoryResults": [{"categoryId": category, "results": []} for category in categories]}


if __name__ == "__main__":
    unittest.main()
