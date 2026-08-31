from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREPARE = load_module("seo_prepare", ROOT / "deploy" / "prepare.py")
RELEASE = load_module("seo_release", ROOT / "tools" / "build_release.py")


class PackagingTest(unittest.TestCase):
    def test_manifest_checker_is_exact_canonical_copy(self) -> None:
        canonical = ROOT.parents[1] / "work" / "extella-agent-standards" / "templates" / "manifest_check.py"
        actual = (ROOT / "manifest_check.py").read_bytes()
        if canonical.is_file():
            self.assertEqual(actual, canonical.read_bytes())
        else:
            self.assertEqual(hashlib.sha256(actual).hexdigest(), "bebfd4a28d0df5b32855901451371bb74cd39de98c6d87f418979d3e953ea201")

    def test_runtime_payload_contains_installer_and_canonical_manifest_check(self) -> None:
        payload = RELEASE.payload_files()
        self.assertEqual(RELEASE.VERSION, "2.0.2")
        for relative in (
            ".gitattributes",
            "README.md",
            "README.en.md",
            "THIRD_PARTY_NOTICES.md",
            "assets/readme/architecture-en.svg",
            "assets/readme/architecture-ru.svg",
            "assets/readme/hero-en.svg",
            "assets/readme/hero-ru.svg",
        ):
            self.assertIn(relative, payload)
        self.assertIn("install.py", payload)
        self.assertIn("MANIFEST.yaml", payload)
        self.assertIn("manifest_check.py", payload)
        self.assertIn("deploy/agent-zero-profile/usr/agents/seo_employee_no_tools/plugins/_tool_access/config.json", payload)
        self.assertNotIn("deploy/bindings/agent_binding.json", payload)
        self.assertNotIn("deploy/.env", payload)
        self.assertFalse(any(path.startswith("docs/investor/") for path in payload))
        self.assertFalse(any(path.startswith("docs/plans/") for path in payload))
        self.assertFalse(any(path.startswith("evidence/") for path in payload))
        self.assertFalse(any(path.startswith("patches/extella-agent-standards-") for path in payload))
        self.assertFalse(any(path.startswith("docs/compliance/") for path in payload))
        self.assertNotIn("docs/verification/v2-progress.md", payload)

    def test_gitattributes_keeps_text_lf_and_release_binaries_binary(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        self.assertIn("* text=auto eol=lf", attributes)
        self.assertIn("*.svg text eol=lf", attributes)
        self.assertIn("manifest_check.py -text -eol -whitespace", attributes)
        self.assertIn("*.png binary", attributes)
        self.assertIn("*.zip binary", attributes)

    def test_zip_writer_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "README.md").write_bytes(b"release\n")
            (source / "icon.png").write_bytes(b"\x89PNG\r\n")
            first = Path(directory) / "first.zip"
            second = Path(directory) / "second.zip"

            first_hash = RELEASE.write_zip(first, source, ["icon.png", "README.md"], set())
            second_hash = RELEASE.write_zip(second, source, ["README.md", "icon.png"], set())

            self.assertEqual(first_hash, second_hash)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), ["README.md", "icon.png"])
                self.assertEqual(archive.getinfo("README.md").date_time, RELEASE.EPOCH)

    def test_generated_json_is_written_with_lf_on_every_platform(self) -> None:
        paths = [ROOT / "release-manifest.json"]
        build_record = ROOT / "dist" / "build.json"
        if build_record.is_file():
            paths.append(build_record)
        for path in paths:
            self.assertNotIn(b"\r\n", path.read_bytes())

    def test_docker_context_excludes_generated_bindings_and_local_env(self) -> None:
        rules = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("deploy/bindings/*", rules)
        self.assertIn("deploy/.env", rules)

    def test_bindings_are_strict_and_do_not_assert_profile_before_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(PREPARE, "BINDINGS", Path(directory)):
            PREPARE.write_bindings("ct160-seo-01", "client_server", "ct160", "agent_seo_employee")
            device = json.loads((Path(directory) / "device_binding.json").read_text(encoding="utf-8"))
            agent = json.loads((Path(directory) / "agent_binding.json").read_text(encoding="utf-8"))
            assertion_exists = (Path(directory) / "agent_zero_no_tools_profile.json").exists()
        self.assertEqual(set(device), {"device_id", "host", "hosting_profile", "since"})
        self.assertEqual(agent, {"agent_id": "agent_seo_employee"})
        self.assertFalse(assertion_exists)

    def test_binding_rejects_noncanonical_agent_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(PREPARE, "BINDINGS", Path(directory)):
            with self.assertRaisesRegex(RuntimeError, "Extella agent id"):
                PREPARE.write_bindings("ct160-seo-01", "server", "ct160", "not-an-agent")

    def test_no_tools_profile_assets_use_actual_agent_zero_layout(self) -> None:
        base = ROOT / "deploy" / "agent-zero-profile" / "usr" / "agents" / PREPARE.PROFILE_ID / "plugins"
        tool_policy = json.loads((base / "_tool_access" / "config.json").read_text(encoding="utf-8"))
        skill_policy = json.loads((base / "_skills" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(tool_policy, PREPARE.profile_assertion()["tool_policy"])
        self.assertEqual(skill_policy, {"visibility_policy": PREPARE.profile_assertion()["skill_policy"]})

    def test_profile_assertion_is_written_only_after_exact_container_readback(self) -> None:
        profile_root = ROOT / "deploy" / "agent-zero-profile"
        outputs = [
            (profile_root / "usr" / "agents" / PREPARE.PROFILE_ID / relative).read_bytes()
            for relative in PREPARE.PROFILE_FILES
        ]
        completed = [mock.Mock(stdout=value) for value in outputs]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(PREPARE, "BINDINGS", Path(directory)), \
                mock.patch.object(PREPARE, "run"), \
                mock.patch.object(PREPARE.subprocess, "run", side_effect=completed):
            PREPARE.provision_no_tools_profile("agent-zero")
            assertion = json.loads(
                (Path(directory) / "agent_zero_no_tools_profile.json").read_text(encoding="utf-8")
            )
        self.assertEqual(assertion, PREPARE.profile_assertion())


if __name__ == "__main__":
    unittest.main()
