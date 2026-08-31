from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("extella_backup", ROOT / "deploy" / "backup.py")
assert SPEC is not None and SPEC.loader is not None
BACKUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKUP)


def _tar(path: Path, filename: str = "data.json", content: bytes = b"{}\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as archive:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.mode = 0o700
        archive.addfile(root)
        info = tarfile.TarInfo(filename)
        info.size = len(content)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(content))
    os.chmod(path, 0o600)


def _snapshot(root: Path, snapshot_id: str = "snapshot") -> Path:
    snapshot = root / snapshot_id
    snapshot.mkdir(parents=True)
    volumes: list[dict[str, object]] = []
    names = {logical: f"project_{logical}" for logical in BACKUP.PRODUCT_VOLUMES}
    for logical in BACKUP.PRODUCT_VOLUMES:
        archive = snapshot / "volumes" / f"{logical}.tar"
        _tar(archive, f"{logical}.json", b'{"ok":true}\n')
        item = BACKUP._artifact(archive, kind="volume", name=logical, logical_volume=logical)
        item["path"] = f"volumes/{logical}.tar"
        volumes.append(item)
    postgres = snapshot / "postgres" / "crawlseo.sql"
    postgres.parent.mkdir(parents=True, exist_ok=True)
    postgres.write_bytes(b"-- logical dump\n")
    os.chmod(postgres, 0o600)
    postgres_item = BACKUP._artifact(postgres, kind="postgres_dump", name=BACKUP.DATABASE_NAME)
    postgres_item["path"] = "postgres/crawlseo.sql"
    BACKUP._atomic_json(
        snapshot / "manifest.json",
        BACKUP._manifest(snapshot_id, Path("compose.yaml"), names, volumes, postgres_item),
    )
    os.chmod(snapshot, 0o700)
    return snapshot


class BackupRestoreTest(unittest.TestCase):
    def test_verify_and_restore_check_are_offline_and_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = _snapshot(root)
            verified = BACKUP.verify_snapshot(snapshot, root)
            self.assertEqual(verified["status"], "verified")

            with mock.patch.object(BACKUP, "_run", side_effect=AssertionError("Docker must not run")):
                result = BACKUP.restore_check(snapshot, root)
            self.assertTrue(result["temporary_only"])
            self.assertFalse(any(path.name.startswith("extella-restore-check-") for path in root.iterdir()))

    def test_verify_rejects_manifest_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = _snapshot(root)
            manifest_path = snapshot / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][0]["path"] = "volumes/../outside.tar"
            with self.assertRaises(BACKUP.BackupError):
                BACKUP._validate_manifest(manifest)

    def test_verify_rejects_hash_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = _snapshot(root)
            artifact = snapshot / "postgres" / "crawlseo.sql"
            artifact.write_bytes(b"tampered\n")
            with self.assertRaises(BACKUP.BackupError):
                BACKUP.verify_snapshot(snapshot, root)

    def test_prune_is_dry_run_without_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _snapshot(root, "one")
            _snapshot(root, "two")
            _snapshot(root, "three")
            result = BACKUP.prune_snapshots(root, keep=1)
            self.assertTrue(result["dry_run"])
            self.assertEqual(len(result["planned"]), 2)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["one", "three", "two"])

    def test_create_archives_only_product_scope_and_restores_running_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            running = {"seo-employee", BACKUP.DATABASE_SERVICE}

            def dump(_compose: Path, destination: Path, *, project_name: str | None = None) -> None:
                self.assertIsNone(project_name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"-- logical dump\n")
                os.chmod(destination, 0o600)

            def archive(_logical: str, _actual: str, _image: str, destination: Path) -> None:
                _tar(destination)

            with mock.patch.object(
                BACKUP,
                "_compose_metadata",
                return_value=({logical: f"project_{logical}" for logical in BACKUP.PRODUCT_VOLUMES}, "product:1"),
            ), mock.patch.object(BACKUP, "_running_services", return_value=running), mock.patch.object(
                BACKUP, "_stop_non_database"
            ) as stop_non_db, mock.patch.object(
                BACKUP, "_start_database_if_needed", return_value=False
            ), mock.patch.object(BACKUP, "_database_dump", side_effect=dump), mock.patch.object(
                BACKUP, "_stop_database"
            ) as stop_db, mock.patch.object(BACKUP, "_archive_volume", side_effect=archive), mock.patch.object(
                BACKUP, "_restore_service_state"
            ) as restore_state:
                snapshot = BACKUP.create_snapshot(root, Path("compose.yaml"))

            verified = BACKUP.verify_snapshot(snapshot, root)
            self.assertEqual(verified["artifacts"], 6)
            stop_non_db.assert_called_once_with(Path("compose.yaml"), running, project_name=None)
            stop_db.assert_called_once_with(Path("compose.yaml"), project_name=None)
            restore_state.assert_called_once_with(Path("compose.yaml"), running, project_name=None)
            manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
            categories = {item["category"] for item in manifest["scope"]["excluded_non_sensitive_categories"]}
            self.assertIn("deploy/secrets", categories)
            self.assertIn("deploy/bindings", categories)
            self.assertIn("agent_zero_usr", categories)
            self.assertIn("crawlseo_db_raw_volume", categories)

    def test_create_with_project_name_never_archives_default_project_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_name = "extella-seo-release"
            compose_file = Path("compose.yaml")
            commands: list[tuple[str, ...]] = []
            archived_volumes: list[str] = []

            def run(args: list[str], **_kwargs: object) -> object:
                commands.append(tuple(args))
                if "config" in args:
                    payload = {
                        "name": project_name,
                        "volumes": {logical: {} for logical in BACKUP.PRODUCT_VOLUMES},
                        "services": {"seo-employee": {"image": "product:1"}},
                    }
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload).encode())
                if "ps" in args:
                    return subprocess.CompletedProcess(args, 0, stdout=b"[]")
                return subprocess.CompletedProcess(args, 0, stdout=b"")

            def dump(_compose: Path, destination: Path, *, project_name: str | None = None) -> None:
                self.assertEqual(project_name, "extella-seo-release")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"-- logical dump\n")
                os.chmod(destination, 0o600)

            def archive(_logical: str, actual: str, _image: str, destination: Path) -> None:
                archived_volumes.append(actual)
                _tar(destination)

            with mock.patch.object(BACKUP, "_run", side_effect=run), mock.patch.object(
                BACKUP, "_database_dump", side_effect=dump
            ), mock.patch.object(BACKUP, "_archive_volume", side_effect=archive):
                snapshot = BACKUP.create_snapshot(root, compose_file, project_name=project_name)

            self.assertEqual(
                archived_volumes,
                [f"{project_name}_{logical}" for logical in BACKUP.PRODUCT_VOLUMES],
            )
            self.assertNotIn("deploy_seo_config", archived_volumes)
            compose_commands = [command for command in commands if command[:2] == ("docker", "compose")]
            self.assertTrue(compose_commands)
            for command in compose_commands:
                self.assertEqual(command[2:4], ("--project-name", project_name))
            manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["volume_names"]["seo_config"], "extella-seo-release_seo_config")


if __name__ == "__main__":
    unittest.main()
