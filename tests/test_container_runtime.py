from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experts"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROXY = load("extella_source_proxy", ROOT / "runtime" / "source_proxy.py")
SERVER = load("extella_product_server", ROOT / "runtime" / "product" / "server.py")
BOOTSTRAP = load("extella_product_bootstrap", ROOT / "runtime" / "product" / "bootstrap.py")
GATEWAY = load("extella_product_gateway", ROOT / "runtime" / "product" / "gateway.py")
PREPARE = load("extella_deploy_prepare", ROOT / "deploy" / "prepare.py")
INSTALL = load("extella_installer", ROOT / "install.py")
from seo_employee_sources import CrawlSEOAdapter


class _SourceStub(BaseHTTPRequestHandler):
    received: dict[str, object] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received = json.loads(self.rfile.read(length))
        payload = (
            {"addresses": ["93.184.216.34"]}
            if self.path == "/resolve"
            else {"crawl": {"pagesFound": 1}, "issues": []}
        )
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ContainerRuntimeTest(unittest.TestCase):
    def test_source_proxy_calls_internal_worker_and_writes_atomically(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SourceStub)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "crawlseo.json"
                plan = Path(directory) / "plan.json"
                plan.write_text(
                    json.dumps(
                        {
                            "max_pages": 1,
                            "categories": ["core", "links"],
                            "performance_sample_pages": 1,
                            "timeout_ms": 120000,
                        }
                    ),
                    encoding="utf-8",
                )
                with mock.patch.dict(
                    PROXY.ENDPOINTS,
                    {"CrawlSEO": f"http://127.0.0.1:{server.server_port}/run"},
                ):
                    PROXY.proxy_source("CrawlSEO", "https://example.com/", plan, output)
                self.assertEqual(
                    _SourceStub.received,
                    {
                        "site_url": "https://example.com/",
                        "plan": {
                            "max_pages": 1,
                            "categories": ["core", "links"],
                            "performance_sample_pages": 1,
                            "timeout_ms": 120000,
                        },
                    },
                )
                self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["crawl"]["pagesFound"], 1)
                self.assertEqual(list(output.parent.glob("*.tmp")), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_source_proxy_rejects_an_invalid_plan_before_network_io(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            plan.write_text('{"max_pages":101}', encoding="utf-8")
            with mock.patch("urllib.request.urlopen") as open_url:
                with self.assertRaisesRegex(ValueError, "plan is invalid"):
                    PROXY.proxy_source("CrawlSEO", "https://example.com/", plan, Path(directory) / "output.json")
            open_url.assert_not_called()

    def test_source_proxy_preserves_only_allowlisted_worker_machine_reasons(self) -> None:
        plan_payload = {
            "max_pages": 1,
            "categories": ["core", "links"],
            "performance_sample_pages": 1,
            "timeout_ms": 120000,
        }
        for reason in ("waf", "captcha", "http_403", "http_429", "http_503", "robots_denied", "timeout"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                plan = Path(directory) / "plan.json"
                output = Path(directory) / "output.json"
                plan.write_text(json.dumps(plan_payload), encoding="utf-8")
                error = urllib.error.HTTPError(
                    "http://crawlseo:8081/run", 400, "bad request", {}, io.BytesIO(json.dumps({"code": reason}).encode())
                )
                with mock.patch("urllib.request.urlopen", side_effect=error):
                    PROXY.proxy_source("CrawlSEO", "https://example.com/", plan, output)
                payload = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(payload, {"reason": reason, "status": "unavailable"})
                result = CrawlSEOAdapter().parse(payload, SimpleNamespace(
                    max_pages=1, categories=("core", "links"), performance_sample_pages=1, required_sources=("CrawlSEO",)
                ))
                self.assertEqual((result.status, result.reason), ("unavailable", reason))

    def test_worker_dns_result_still_passes_public_address_validation(self) -> None:
        service = __import__("seo_employee_service")
        with mock.patch("urllib.request.urlopen") as open_url:
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = b'{"addresses":["93.184.216.34"]}'
            open_url.return_value = response
            resolved = service._worker_resolver(
                "example.com", None, type=1, endpoint="http://dns-resolver:8083/resolve"
            )
        self.assertEqual(resolved[0][4][0], "93.184.216.34")
        for endpoint in ("http://crawlseo:8081/resolve", "http://dns-resolver:8084/resolve"):
            with self.subTest(endpoint=endpoint), self.assertRaises(OSError):
                service._worker_resolver("example.com", None, type=1, endpoint=endpoint)

    def test_product_dispatch_is_fixed_and_token_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token"
            token_file.write_text("short", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "too short"):
                SERVER.read_token(token_file)

        with mock.patch.object(SERVER, "seo_employee_state", return_value='{"state":"ready"}'):
            self.assertEqual(SERVER.dispatch("GET", "/api/state", {}), (200, {"state": "ready"}))
        with mock.patch.object(
            SERVER,
            "seo_employee_run",
            return_value='{"status":"success","method":"run","state":"queued","queue_item":{},"duplicate":false}',
        ) as run:
            status, payload = SERVER.dispatch("POST", "/api/run", {"target_id": "target-example-com-0f115db0"})
        self.assertEqual((status, payload["state"]), (202, "queued"))
        self.assertEqual(run.call_args.kwargs["trigger"], "manual")
        self.assertEqual(SERVER.dispatch("POST", "/api/run", {"prompt": "ignored"})[0], 400)

        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "secret"
            secret.write_text("x" * 32 + "\n", encoding="utf-8")
            self.assertEqual(BOOTSTRAP.read_secret(secret, 32), "x" * 32)

    def test_prepare_accepts_the_documented_sixteen_character_agent_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            PREPARE, "SECRETS", Path(directory)
        ):
            token = Path(directory) / "agent_zero_api_key"
            token.write_text("a" * 16 + "\n", encoding="utf-8")
            PREPARE.ensure_generated_secret("agent_zero_api_key", 16)
            self.assertEqual(token.read_text(encoding="utf-8").strip(), "a" * 16)

    def test_prepare_starts_restarts_and_checks_loopback_health(self) -> None:
        compose = ("docker", "compose", "-f", str(PREPARE.COMPOSE))
        with mock.patch.object(PREPARE, "run") as run, mock.patch.object(PREPARE, "wait_for_product_health") as health:
            PREPARE.start_and_verify()
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(*compose, "up", "-d"),
                mock.call(*compose, "restart", "seo-employee", "api-gateway"),
            ],
        )
        health.assert_called_once_with()

    def test_prepare_health_probe_is_loopback_only(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with mock.patch.object(PREPARE.urllib.request, "urlopen", return_value=response) as open_url:
            PREPARE.wait_for_product_health()
        open_url.assert_called_once_with("http://127.0.0.1:8088/health", timeout=3)

    def test_prepare_runtime_rollback_retains_old_images_and_restarts_prior_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yaml"
            compose.write_text("services: {}\n", encoding="utf-8")
            state = {
                "schema": PREPARE.RUNTIME_STATE_SCHEMA,
                "project": "extella-seo-employee",
                "images": [
                    {"reference": "extella-seo-employee:2.0.1", "image_id": "sha256:" + "a" * 64},
                    {"reference": "postgres@sha256:" + "b" * 64, "image_id": "sha256:" + "b" * 64},
                ],
                "health_url": "http://127.0.0.1:9191/health",
            }
            with (
                mock.patch.object(PREPARE, "run") as run,
                mock.patch.object(PREPARE, "wait_for_product_health") as health,
                mock.patch.dict(PREPARE.os.environ, {"SEO_EMPLOYEE_PORT": "8088"}, clear=False),
            ):
                PREPARE.restore_runtime_state(state, compose)
        self.assertEqual(
            run.call_args_list[0],
            mock.call("docker", "tag", "sha256:" + "a" * 64, "extella-seo-employee:2.0.1"),
        )
        self.assertEqual(
            run.call_args_list[1].args,
            ("docker", "compose", "--project-name", "extella-seo-employee", "-f", str(compose), "up", "-d"),
        )
        self.assertEqual(run.call_args_list[1].kwargs["env"]["SEO_EMPLOYEE_PORT"], "9191")
        health.assert_called_once_with(url="http://127.0.0.1:9191/health")

    def test_prepare_captures_the_existing_api_gateway_loopback_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yaml"
            compose.write_text("services: {}\n", encoding="utf-8")
            with mock.patch.object(
                PREPARE,
                "run",
                side_effect=[
                    "container-id",
                    '{"com.docker.compose.project":"extella-seo-employee","com.docker.compose.service":"api-gateway"}',
                    '{"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"9191"}]}',
                    "extella-seo-employee:2.0.1",
                    "sha256:" + "a" * 64,
                ],
            ):
                state = PREPARE.capture_runtime_state(compose)
        self.assertEqual(state["health_url"], "http://127.0.0.1:9191/health")

    def test_prepare_rejects_a_non_loopback_runtime_health_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yaml"
            compose.write_text("services: {}\n", encoding="utf-8")
            state = {
                "schema": PREPARE.RUNTIME_STATE_SCHEMA,
                "project": "extella-seo-employee",
                "images": [{"reference": "extella-seo-employee:2.0.1", "image_id": "sha256:" + "a" * 64}],
                "health_url": "http://example.com:9191/health",
            }
            with self.assertRaisesRegex(RuntimeError, "loopback health URL"):
                PREPARE.restore_runtime_state(state, compose)

    def test_installer_reuses_device_binding_and_runs_prepare_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "installed"
            binding = target / "deploy" / "bindings" / "device_binding.json"
            binding.parent.mkdir(parents=True)
            binding.write_text(
                json.dumps(
                    {
                        "device_id": "device-seo-01",
                        "host": "seo-host",
                        "hosting_profile": "client_server",
                        "since": "2026-08-31T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with (
                mock.patch.object(INSTALL, "TARGET", target),
                mock.patch.object(INSTALL, "BACKUPS", Path(directory) / "backups"),
                mock.patch.object(INSTALL.os, "name", "posix"),
                mock.patch.object(INSTALL, "run_manifest_check", return_value=True),
                mock.patch.object(INSTALL, "release_manifest", return_value={}),
                mock.patch.object(INSTALL, "install_payload", return_value=None),
                mock.patch.object(INSTALL.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(INSTALL.subprocess, "run") as run,
                mock.patch.dict(
                    INSTALL.os.environ,
                    {
                        "EXTELLA_AGENT_ID": "agent_seo_employee",
                        "EXTELLA_APP_NAME": "Extella SEO Employee",
                        "EXTELLA_APP_VERSION": "2.0.3",
                    },
                    clear=False,
                ),
                mock.patch("sys.stdout", output),
            ):
                self.assertEqual(INSTALL.main(), 0)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0], ("docker", "info"))
            self.assertEqual(
                commands[1],
                (
                    sys.executable,
                    str(target / "deploy" / "prepare.py"),
                    "--device-id",
                    "device-seo-01",
                    "--hosting-profile",
                    "client_server",
                    "--host",
                    "seo-host",
                    "--agent-id",
                    "agent_seo_employee",
                ),
            )
            self.assertEqual(run.call_args_list[1].kwargs["stdout"], INSTALL.subprocess.DEVNULL)
            self.assertEqual(run.call_args_list[1].kwargs["stderr"], INSTALL.subprocess.DEVNULL)
            self.assertEqual(json.loads(output.getvalue())["code"], "installed_and_healthy")

    def test_installer_rejects_unbound_device_before_payload_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with (
                mock.patch.object(INSTALL, "TARGET", Path(directory) / "installed"),
                mock.patch.object(INSTALL.os, "name", "posix"),
                mock.patch.object(INSTALL, "run_manifest_check", return_value=True),
                mock.patch.object(INSTALL, "install_payload") as install_payload,
                mock.patch.dict(
                    INSTALL.os.environ,
                    {
                        "EXTELLA_AGENT_ID": "agent_seo_employee",
                        "EXTELLA_APP_NAME": "Extella SEO Employee",
                        "EXTELLA_APP_VERSION": "2.0.3",
                    },
                    clear=False,
                ),
                mock.patch("sys.stdout", output),
            ):
                self.assertEqual(INSTALL.main(), 1)
            install_payload.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["code"], "extella_device_binding_required")

    def test_installer_rolls_back_replaced_and_created_payload_files_after_copy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "installed"
            source.mkdir()
            (source / "existing.txt").write_bytes(b"new existing")
            (source / "new.txt").write_bytes(b"new file")
            (source / "release-manifest.json").write_bytes(b"new manifest")
            (target / "deploy" / "bindings").mkdir(parents=True)
            (target / "existing.txt").write_bytes(b"old existing")
            (target / "deploy" / "bindings" / "device_binding.json").write_text(
                json.dumps(
                    {
                        "device_id": "device-seo-01",
                        "host": "seo-host",
                        "hosting_profile": "client_server",
                        "since": "2026-08-31T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            original_copy = INSTALL.atomic_copy
            original_cwd = Path.cwd()

            def fail_mid_copy(source_path: Path, destination: Path) -> None:
                if source_path == source / "new.txt":
                    raise OSError("simulated payload copy failure")
                original_copy(source_path, destination)

            try:
                with (
                    mock.patch.object(INSTALL, "HERE", source),
                    mock.patch.object(INSTALL, "TARGET", target),
                    mock.patch.object(INSTALL, "BACKUPS", root / "backups"),
                    mock.patch.object(INSTALL.os, "name", "posix"),
                    mock.patch.object(INSTALL, "run_manifest_check", return_value=True),
                    mock.patch.object(INSTALL, "release_manifest", return_value={"existing.txt": "x", "new.txt": "y"}),
                    mock.patch.object(INSTALL, "atomic_copy", side_effect=fail_mid_copy),
                    mock.patch.object(INSTALL.shutil, "which", return_value="/usr/bin/docker"),
                    mock.patch.object(INSTALL.subprocess, "run"),
                    mock.patch.dict(
                        INSTALL.os.environ,
                        {
                            "EXTELLA_AGENT_ID": "agent_seo_employee",
                            "EXTELLA_APP_NAME": "Extella SEO Employee",
                            "EXTELLA_APP_VERSION": "2.0.3",
                        },
                        clear=False,
                    ),
                    mock.patch("sys.stdout", output),
                ):
                    self.assertEqual(INSTALL.main(), 1)
            finally:
                os.chdir(original_cwd)

            result = json.loads(output.getvalue())
            backup = Path(result["backup"])
            self.assertEqual(result["code"], "install_or_deployment_failed")
            self.assertTrue(result["rolled_back"])
            self.assertEqual((target / "existing.txt").read_bytes(), b"old existing")
            self.assertFalse((target / "new.txt").exists())
            self.assertEqual((backup / "existing.txt").read_bytes(), b"old existing")
            self.assertEqual(json.loads((backup / "rollback.json").read_text(encoding="utf-8"))["created"], ["new.txt"])

    def test_installer_restores_manifest_and_prepare_bindings_after_prepare_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "installed"
            source.mkdir()
            (source / "existing.txt").write_bytes(b"new existing")
            (source / "new.txt").write_bytes(b"new file")
            (source / "release-manifest.json").write_bytes(b"new manifest")
            bindings = target / "deploy" / "bindings"
            bindings.mkdir(parents=True)
            original_device = (
                b'{"device_id":"device-seo-01","host":"seo-host",'
                b'"hosting_profile":"client_server","since":"old"}\n'
            )
            original_agent = b'{"agent_id":"agent_previous"}\n'
            (target / "existing.txt").write_bytes(b"old existing")
            (target / "release-manifest.json").write_bytes(b"old manifest")
            (bindings / "device_binding.json").write_bytes(original_device)
            (bindings / "agent_binding.json").write_bytes(original_agent)
            output = io.StringIO()
            original_cwd = Path.cwd()
            runtime_snapshot = root / "runtime-state.json"

            def fail_prepare(_device_binding: dict[str, str], _agent_id: str) -> None:
                (bindings / "device_binding.json").write_bytes(b"changed device")
                (bindings / "agent_binding.json").write_bytes(b"changed agent")
                (bindings / "agent_zero_no_tools_profile.json").write_bytes(b"new assertion")
                raise RuntimeError("simulated prepare failure")

            def restore_after_files(snapshot: Path) -> None:
                self.assertEqual(snapshot, runtime_snapshot)
                self.assertEqual((target / "release-manifest.json").read_bytes(), b"old manifest")
                self.assertEqual((bindings / "device_binding.json").read_bytes(), original_device)
                self.assertEqual((bindings / "agent_binding.json").read_bytes(), original_agent)

            try:
                with (
                    mock.patch.object(INSTALL, "HERE", source),
                    mock.patch.object(INSTALL, "TARGET", target),
                    mock.patch.object(INSTALL, "BACKUPS", root / "backups"),
                    mock.patch.object(INSTALL.os, "name", "posix"),
                    mock.patch.object(INSTALL, "run_manifest_check", return_value=True),
                    mock.patch.object(INSTALL, "release_manifest", return_value={"existing.txt": "x", "new.txt": "y"}),
                    mock.patch.object(INSTALL.shutil, "which", return_value="/usr/bin/docker"),
                    mock.patch.object(INSTALL.subprocess, "run"),
                    mock.patch.object(INSTALL, "capture_runtime_state", return_value=runtime_snapshot) as capture_runtime,
                    mock.patch.object(INSTALL, "prepare_and_start", side_effect=fail_prepare),
                    mock.patch.object(INSTALL, "restore_runtime_state", side_effect=restore_after_files) as restore_runtime,
                    mock.patch.dict(
                        INSTALL.os.environ,
                        {
                            "EXTELLA_AGENT_ID": "agent_seo_employee",
                            "EXTELLA_APP_NAME": "Extella SEO Employee",
                            "EXTELLA_APP_VERSION": "2.0.3",
                        },
                        clear=False,
                    ),
                    mock.patch("sys.stdout", output),
                ):
                    self.assertEqual(INSTALL.main(), 1)
            finally:
                os.chdir(original_cwd)

            result = json.loads(output.getvalue())
            backup = Path(result["backup"])
            self.assertEqual(result["code"], "install_or_deployment_failed")
            self.assertTrue(result["rolled_back"])
            self.assertEqual((target / "existing.txt").read_bytes(), b"old existing")
            self.assertEqual((target / "release-manifest.json").read_bytes(), b"old manifest")
            self.assertEqual((bindings / "device_binding.json").read_bytes(), original_device)
            self.assertEqual((bindings / "agent_binding.json").read_bytes(), original_agent)
            self.assertFalse((target / "new.txt").exists())
            self.assertFalse((bindings / "agent_zero_no_tools_profile.json").exists())
            self.assertEqual((backup / "release-manifest.json").read_bytes(), b"old manifest")
            capture_runtime.assert_called_once()
            restore_runtime.assert_called_once_with(runtime_snapshot)

    def test_installer_reports_false_when_runtime_rollback_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "installed"
            source.mkdir()
            (source / "release-manifest.json").write_bytes(b"new manifest")
            bindings = target / "deploy" / "bindings"
            bindings.mkdir(parents=True)
            (bindings / "device_binding.json").write_text(
                json.dumps(
                    {
                        "device_id": "device-seo-01",
                        "host": "seo-host",
                        "hosting_profile": "client_server",
                        "since": "2026-08-31T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            original_cwd = Path.cwd()
            try:
                with (
                    mock.patch.object(INSTALL, "HERE", source),
                    mock.patch.object(INSTALL, "TARGET", target),
                    mock.patch.object(INSTALL, "BACKUPS", root / "backups"),
                    mock.patch.object(INSTALL.os, "name", "posix"),
                    mock.patch.object(INSTALL, "run_manifest_check", return_value=True),
                    mock.patch.object(INSTALL, "release_manifest", return_value={}),
                    mock.patch.object(INSTALL, "capture_runtime_state", return_value=root / "runtime-state.json"),
                    mock.patch.object(INSTALL.shutil, "which", return_value="/usr/bin/docker"),
                    mock.patch.object(INSTALL.subprocess, "run"),
                    mock.patch.object(INSTALL, "prepare_and_start", side_effect=RuntimeError("health failed")),
                    mock.patch.object(INSTALL, "restore_runtime_state", side_effect=RuntimeError("restore failed")),
                    mock.patch.dict(
                        INSTALL.os.environ,
                        {
                            "EXTELLA_AGENT_ID": "agent_seo_employee",
                            "EXTELLA_APP_NAME": "Extella SEO Employee",
                            "EXTELLA_APP_VERSION": "2.0.3",
                        },
                        clear=False,
                    ),
                    mock.patch("sys.stdout", output),
                ):
                    self.assertEqual(INSTALL.main(), 1)
            finally:
                os.chdir(original_cwd)

            result = json.loads(output.getvalue())
            self.assertEqual(result["code"], "install_or_deployment_failed")
            self.assertFalse(result["rolled_back"])
            self.assertFalse(result["runtime_rolled_back"])

    def test_gateway_allows_only_canonical_multi_target_reads(self) -> None:
        self.assertEqual(GATEWAY.request_target("product", "GET", "/api/targets"), "/api/targets")
        self.assertEqual(
            GATEWAY.request_target("product", "GET", "/api/state?target_id=target-example-1"),
            "/api/state?target_id=target-example-1",
        )
        for target in (
            "/api/state?target_id=",
            "/api/state?target_id=one&extra=two",
            "/api/state?target_id=../../etc",
            "http://example.com/api/state",
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                GATEWAY.request_target("product", "GET", target)

    def test_compose_isolates_egress_and_enforces_runtime_limits(self) -> None:
        compose = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
        product = compose.split("\n  api-gateway:", 1)[0]
        self.assertNotIn("\n    ports:", product)
        self.assertIn("networks: [control, crawlseo_control, resolver_control, seomator_control]", product)
        self.assertIn("EXTELLA_DNS_RESOLVER_URL: http://dns-resolver:8083/resolve", product)
        self.assertIn("dns-resolver:\n        condition: service_healthy", product)
        self.assertIn("- ./bindings:/run/bindings:ro", product)
        self.assertIn("EXTELLA_DEVICE_BINDING_FILE: /run/bindings/device_binding.json", product)
        self.assertIn("EXTELLA_AGENT_BINDING_FILE: /run/bindings/agent_binding.json", product)
        self.assertIn("EXTELLA_AGENT_ZERO_NO_TOOLS_PROFILE: seo_employee_no_tools", product)
        self.assertIn(
            "EXTELLA_AGENT_ZERO_NO_TOOLS_ASSERTION_FILE: /run/bindings/agent_zero_no_tools_profile.json",
            product,
        )
        self.assertIn("healthcheck:", product)
        self.assertIn("http://127.0.0.1:8080/health", product)
        self.assertIn("start_period: 10s", product)
        self.assertIn('"127.0.0.1:${SEO_EMPLOYEE_PORT:-8088}:8080"', compose)
        self.assertIn("  agent_zero_usr: {}", compose)
        self.assertNotIn("AGENT_ZERO_VOLUME", compose)
        self.assertNotIn("docker.sock", compose)
        self.assertNotIn("egress", product)
        self.assertIn("networks: [control, agent_zero_egress]", compose)
        self.assertIn("networks: [crawlseo_control, crawlseo_db, crawlseo_egress]", compose)
        resolver = compose.split("\n  dns-resolver:\n", 1)[1].split("\n  seomator:\n", 1)[0]
        self.assertIn("image: extella-seo-crawlseo:2.0.0", resolver)
        self.assertIn("entrypoint: [node, /app/extella_worker_server.mjs]", resolver)
        self.assertIn("EXTELLA_WORKER_KIND: Resolver", resolver)
        self.assertIn("EXTELLA_WORKER_PORT: 8083", resolver)
        self.assertIn("networks: [resolver_control, resolver_egress]", resolver)
        self.assertIn("read_only: true", resolver)
        self.assertIn("cap_drop: [ALL]", resolver)
        self.assertIn("security_opt: [no-new-privileges:true]", resolver)
        self.assertIn('cpus: "0.10"', resolver)
        self.assertIn("mem_limit: 64m", resolver)
        self.assertIn("pids_limit: 32", resolver)
        self.assertIn("logging: *container_logging", resolver)
        self.assertIn("http://127.0.0.1:8083/health", resolver)
        self.assertNotIn("ports:", resolver)
        self.assertNotIn("secrets:", resolver)
        self.assertNotIn("volumes:", resolver)
        self.assertIn("resolver_control:\n    internal: true", compose)
        self.assertIn("resolver_egress: {}", compose)
        self.assertIn("networks: [seomator_control, seomator_egress]", compose)
        self.assertNotIn("\n  egress:", compose)
        self.assertIn("cpus:", compose)
        self.assertIn("mem_limit:", compose)
        self.assertIn("pids_limit:", compose)
        self.assertIn("max-size: \"10m\"", compose)
        self.assertIn("max-file: \"3\"", compose)
        self.assertNotIn("fetch('http://127.0.0.1:8081/health')", compose)
        self.assertNotIn("fetch('http://127.0.0.1:8082/health')", compose)
        self.assertEqual(
            GATEWAY.validate_upstream("agent-zero", "http://host.docker.internal:50081"),
            "http://host.docker.internal:50081",
        )
        with self.assertRaisesRegex(ValueError, "upstream"):
            GATEWAY.validate_upstream("agent-zero", "https://example.com")

    def test_worker_images_preload_the_safe_fetch_guard(self) -> None:
        safe_fetch = (ROOT / "runtime" / "safe_fetch.mjs").read_text(encoding="utf-8")
        self.assertIn("createSafeFetch", safe_fetch)
        self.assertIn("DNS result is not entirely public", safe_fetch)
        self.assertIn("redirect limit exceeded", safe_fetch)
        for name in ("crawlseo", "seomator"):
            dockerfile = (ROOT / "runtime" / name / "Dockerfile").read_text(encoding="utf-8")
            self.assertIn("COPY extella_safe_fetch.mjs /app/extella_safe_fetch.mjs", dockerfile)
            self.assertIn("EXTELLA_SAFE_FETCH_PRELOAD=1", dockerfile)
            self.assertIn("NODE_OPTIONS=--import=/app/extella_safe_fetch.mjs", dockerfile)
        self.assertIn(
            "mcr.microsoft.com/playwright:v1.57.0-noble@sha256:3bed4b1a12f2338642f3d8cba28e291deef3c66bd4a964bbeb3e57bbff511dbd",
            (ROOT / "runtime" / "seomator" / "Dockerfile").read_text(encoding="utf-8"),
        )
        normalizer = (ROOT / "runtime" / "seomator" / "normalize-package.mjs").read_text(encoding="utf-8")
        self.assertIn("buildDevDependencies", normalizer)
        self.assertNotIn('"electron",', normalizer)
        patch = (ROOT / "patches" / "seomator-ssrf-guard.patch").read_text(encoding="utf-8")
        self.assertIn("await assertPublicUrl(url)", patch)
        self.assertIn("fulfillThroughSafeFetch(route)", patch)
        self.assertNotIn("route.continue()", patch)
        self.assertIn("externalStatusCache", patch)
        self.assertIn("index += 5", patch)
        budget_patch = (ROOT / "patches" / "seomator-external-link-budget.patch").read_text(encoding="utf-8")
        self.assertIn("MAX_EXTERNAL_CHECKS = 20", budget_patch)
        self.assertIn("skippedByBudget", budget_patch)
        seomator_entrypoint = (ROOT / "runtime" / "seomator" / "entrypoint.mjs").read_text(encoding="utf-8")
        self.assertIn("await assertPublicUrl(parsedUrl)", seomator_entrypoint)
        builder = (ROOT / "tools" / "build_runtime.py").read_text(encoding="utf-8")
        self.assertIn('"seomator-ssrf-guard.patch"', builder)

    def test_worker_server_keeps_resolver_dns_only(self) -> None:
        worker_server = (ROOT / "runtime" / "worker_server.mjs").read_text(encoding="utf-8")
        self.assertIn("['CrawlSEO', 'SEOmator', 'Resolver']", worker_server)
        self.assertIn('if (kind === "Resolver") return true;', worker_server)
        self.assertIn('if (kind === "Resolver") {\n    send(response, 404, { status: "error", code: "route_not_found" });', worker_server)

    def test_worker_server_cancels_audit_process_group_on_disconnect(self) -> None:
        worker_server = (ROOT / "runtime" / "worker_server.mjs").read_text(encoding="utf-8")
        self.assertIn('detached: process.platform === "linux"', worker_server)
        self.assertIn('process.kill(-child.pid, "SIGKILL")', worker_server)
        self.assertIn('child.kill("SIGKILL")', worker_server)
        self.assertIn('controller.abort()', worker_server)
        self.assertNotIn('request.once("close", onDisconnect)', worker_server)
        self.assertIn('signal?.removeEventListener("abort", terminate)', worker_server)
        self.assertIn('if (!controller.signal.aborted) send(response, 200, result);', worker_server)
        self.assertIn('if (!responseIsWritable(response)) return false;', worker_server)
        self.assertIn('void handleRequest(request, response).catch(() => {', worker_server)

    def test_entrypoints_and_host_wrappers_keep_the_plan_bounded_and_private(self) -> None:
        crawl_once = (ROOT / "tools" / "crawlseo_once.mjs").read_text(encoding="utf-8")
        seomator = (ROOT / "runtime" / "seomator" / "entrypoint.mjs").read_text(encoding="utf-8")
        self.assertIn("arguments: { siteId, maxPages: plan.max_pages }", crawl_once)
        self.assertIn("crawl.pagesFound <= 0", crawl_once)
        self.assertIn("crawl.pagesFound > plan.max_pages", crawl_once)
        self.assertIn("requested_max_pages: plan.max_pages", crawl_once)
        self.assertIn('"--crawl"', seomator)
        self.assertIn("const EXPENSIVE_CATEGORIES", seomator)
        self.assertIn("const mainCategories = plan.categories.filter", seomator)
        self.assertIn('"--no-cwv"', seomator)
        self.assertIn("deriveSampleUrls", seomator)
        self.assertIn("mergeSampleResults", seomator)
        self.assertIn("categories.join(\",\")", seomator)
        self.assertIn("maxPages: plan.max_pages", seomator)
        self.assertIn("String(maxPages)", seomator)
        self.assertIn("String(cliTimeout(timeoutMs))", seomator)
        for name in ("run_crawlseo", "run_seomator"):
            container_wrapper = (ROOT / "runtime" / "container" / name).read_text(encoding="utf-8")
            self.assertIn("<plan-json>", container_wrapper)
            self.assertIn("runtime/source_proxy.py", container_wrapper)
            host_wrapper = ROOT / "runtime" / name
            if host_wrapper.is_file():
                wrapper = host_wrapper.read_text(encoding="utf-8")
                self.assertIn("<plan-json>", wrapper)
                self.assertIn("dst=/run/plan.json,readonly", wrapper)

    def test_crawlseo_worker_exports_optional_structured_search_performance(self) -> None:
        source = (ROOT / "tools" / "crawlseo_once.mjs").read_text(encoding="utf-8")
        for symbol in ("getSitePeriodMetrics", "getTopKeywords", "getTopPages", "getDailyTraffic", "getAllOpportunities"):
            self.assertIn(symbol, source)
        self.assertIn("search_performance: searchPerformance", source)
        self.assertIn('status: "not_configured"', source)
        worker = (ROOT / "runtime" / "worker_server.mjs").read_text(encoding="utf-8")
        self.assertIn('process.env.EXTELLA_WORKER_KIND === "CrawlSEO"', worker)
        self.assertIn('process.env.TSX_CLI || "/runner/node_modules/tsx/dist/cli.mjs"', worker)

    def test_deployment_probe_uses_v2_target_payloads(self) -> None:
        source = (ROOT / "deploy" / "probe.py").read_text(encoding="utf-8")
        for field in ("target_id", "target_name", "profile", "max_pages", "ownership_confirmed"):
            self.assertIn(f'"{field}"', source)
        self.assertNotIn('{"site_url": args.site_url}', source)


if __name__ == "__main__":
    unittest.main()
