#!/usr/bin/env python3
"""Install and start the verified Extella SEO Employee device payload."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys

from manifest_check import run as run_manifest_check

HERE = pathlib.Path(__file__).resolve().parent
TARGET = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share")) / "extella-seo-employee"
BACKUPS = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")) / "extella-seo-employee" / "backups"
AGENT_ID_RE = re.compile(r"^agent_[A-Za-z0-9_][A-Za-z0-9_-]{2,127}$")
PREPARE_BINDINGS = (
    "device_binding.json",
    "agent_binding.json",
    "agent_zero_no_tools_profile.json",
)
PREPARE_SECRETS = (
    "crawlseo_db_password",
    "seo_employee_api_token",
    "agent_zero_api_key",
)


def emit(status: str, code: str, **extra: object) -> int:
    print(json.dumps({"status": status, "code": code, **extra}, ensure_ascii=False))
    return 0 if status == "success" else 1


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def release_manifest(root: pathlib.Path) -> dict[str, str]:
    document = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    files = document.get("files")
    if document.get("schema") != "extella.seo_employee_release.v1" or not isinstance(files, dict):
        raise ValueError("release-manifest.json has an unsupported schema")
    for relative, expected in files.items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"release file failed integrity check: {relative}")
    return files


def atomic_copy(source: pathlib.Path, destination: pathlib.Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write(destination: pathlib.Path, data: bytes, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


class InstallTransaction:
    """Restore installer-owned files if deployment preparation does not finish."""

    def __init__(self) -> None:
        self._backup: pathlib.Path | None = None
        self._replaced: dict[pathlib.Path, pathlib.Path] = {}
        self._created: set[pathlib.Path] = set()
        self._secret_replaced: dict[pathlib.Path, tuple[bytes, int]] = {}

    @property
    def backup(self) -> pathlib.Path | None:
        return self._backup

    def _ensure_backup(self) -> pathlib.Path:
        if self._backup is None:
            BACKUPS.mkdir(parents=True, exist_ok=True)
            base = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            candidate = BACKUPS / base
            suffix = 1
            while True:
                try:
                    candidate.mkdir()
                    self._backup = candidate
                    break
                except FileExistsError:
                    candidate = BACKUPS / f"{base}-{suffix}"
                    suffix += 1
        return self._backup

    def _backup_for(self, path: pathlib.Path) -> pathlib.Path:
        try:
            relative = path.relative_to(TARGET)
        except ValueError as error:
            raise RuntimeError("transaction path escapes install target") from error
        return self._ensure_backup() / relative

    def runtime_snapshot_path(self) -> pathlib.Path:
        return self._ensure_backup() / "runtime-state.json"

    def track(self, path: pathlib.Path) -> None:
        if path in self._replaced or path in self._created or path in self._secret_replaced:
            return
        if not path.exists():
            self._created.add(path)
            return
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"installer target is not a regular file: {path.relative_to(TARGET)}")
        previous = self._backup_for(path)
        previous.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, previous)
        self._replaced[path] = previous

    def track_secret(self, path: pathlib.Path) -> None:
        if path in self._replaced or path in self._created or path in self._secret_replaced:
            return
        if not path.exists():
            self._created.add(path)
            return
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("installer secret target is not a regular file")
        self._secret_replaced[path] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))

    def track_prepare_outputs(self) -> None:
        bindings = TARGET / "deploy" / "bindings"
        for name in PREPARE_BINDINGS:
            self.track(bindings / name)
        secrets = TARGET / "deploy" / "secrets"
        for name in PREPARE_SECRETS:
            self.track_secret(secrets / name)

    def _write_rollback_metadata(self) -> None:
        if self._backup is None:
            return
        rollback = {
            "target": str(TARGET),
            "release": "2.0.3",
            "replaced": sorted(str(path.relative_to(TARGET)) for path in self._replaced),
            "created": sorted(str(path.relative_to(TARGET)) for path in self._created),
        }
        atomic_write(
            self._backup / "rollback.json",
            (json.dumps(rollback, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            0o600,
        )

    def commit(self) -> None:
        self._write_rollback_metadata()

    def rollback(self) -> None:
        self._write_rollback_metadata()
        errors: list[OSError] = []
        for path in sorted(self._created, key=lambda value: len(value.parts), reverse=True):
            try:
                if path.exists() or path.is_symlink():
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    else:
                        raise OSError("created installer path is not a file")
            except OSError as error:
                errors.append(error)
        for path, previous in self._replaced.items():
            try:
                atomic_copy(previous, path)
            except OSError as error:
                errors.append(error)
        for path, (data, mode) in self._secret_replaced.items():
            try:
                atomic_write(path, data, mode)
            except OSError as error:
                errors.append(error)
        if errors:
            raise errors[0]


def install_payload(files: dict[str, str], transaction: InstallTransaction) -> pathlib.Path | None:
    if HERE == TARGET:
        return None
    for relative in sorted(files):
        source, destination = HERE / relative, TARGET / relative
        transaction.track(destination)
        atomic_copy(source, destination)
    manifest_destination = TARGET / "release-manifest.json"
    transaction.track(manifest_destination)
    atomic_copy(HERE / "release-manifest.json", manifest_destination)
    return transaction.backup


def read_existing_device_binding() -> dict[str, str] | None:
    binding = TARGET / "deploy" / "bindings" / "device_binding.json"
    try:
        value = json.loads(binding.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeError):
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"device_id", "host", "hosting_profile", "since"}
        or not isinstance(value.get("device_id"), str)
        or not isinstance(value.get("host"), str)
        or not isinstance(value.get("hosting_profile"), str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", value["device_id"])
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", value["host"])
        or value["hosting_profile"] not in {"local", "server", "client_server"}
    ):
        return None
    return {
        "device_id": value["device_id"],
        "host": value["host"],
        "hosting_profile": value["hosting_profile"],
    }


def write_agent_binding(agent_id: str, transaction: InstallTransaction) -> None:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise RuntimeError("EXTELLA_AGENT_ID is not a canonical Extella agent id")
    binding = TARGET / "deploy" / "bindings" / "agent_binding.json"
    transaction.track(binding)
    atomic_write(
        binding,
        (json.dumps({"agent_id": agent_id}, sort_keys=True) + "\n").encode("utf-8"),
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
    )


def prepare_and_start(device_binding: dict[str, str], agent_id: str) -> None:
    command = (
        sys.executable,
        str(TARGET / "deploy" / "prepare.py"),
        "--device-id",
        device_binding["device_id"],
        "--hosting-profile",
        device_binding["hosting_profile"],
        "--host",
        device_binding["host"],
        "--agent-id",
        agent_id,
    )
    subprocess.run(command, check=True, cwd=TARGET, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def capture_runtime_state(transaction: InstallTransaction) -> pathlib.Path | None:
    compose = TARGET / "deploy" / "compose.yaml"
    if not compose.is_file():
        return None
    snapshot = transaction.runtime_snapshot_path()
    command = (
        sys.executable,
        str(HERE / "deploy" / "prepare.py"),
        "--capture-runtime-state",
        "--compose-file",
        str(compose),
        "--runtime-state",
        str(snapshot),
    )
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return snapshot


def restore_runtime_state(snapshot: pathlib.Path) -> None:
    command = (
        sys.executable,
        str(HERE / "deploy" / "prepare.py"),
        "--restore-runtime-state",
        "--compose-file",
        str(TARGET / "deploy" / "compose.yaml"),
        "--runtime-state",
        str(snapshot),
    )
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    os.chdir(HERE)
    if not run_manifest_check(HERE / "MANIFEST.yaml"):
        return emit("error", "manifest_check_failed", model_called=False, agent_called=False, paid=False)
    agent_id = os.environ.get("EXTELLA_AGENT_ID", "").strip()
    if not AGENT_ID_RE.fullmatch(agent_id):
        return emit("error", "extella_agent_id_required", model_called=False, agent_called=False, paid=False)
    if not os.environ.get("EXTELLA_APP_NAME", "").strip() or not os.environ.get("EXTELLA_APP_VERSION", "").strip():
        return emit("error", "extella_app_metadata_required", model_called=False, agent_called=False, paid=False)
    if os.name != "posix":
        return emit("error", "linux_required", model_called=False, agent_called=False, paid=False)
    device_binding = read_existing_device_binding()
    if device_binding is None:
        return emit(
            "error",
            "extella_device_binding_required",
            required="a valid existing deploy/bindings/device_binding.json from device enrollment",
            model_called=False,
            agent_called=False,
            paid=False,
        )
    if shutil.which("docker") is None:
        return emit("error", "missing_docker", model_called=False, agent_called=False, paid=False)
    transaction = InstallTransaction()
    runtime_snapshot: pathlib.Path | None = None
    prepare_started = False
    try:
        subprocess.run(("docker", "info"), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        runtime_snapshot = capture_runtime_state(transaction)
        install_payload(release_manifest(HERE), transaction)
        write_agent_binding(agent_id, transaction)
        transaction.track_prepare_outputs()
        prepare_started = True
        prepare_and_start(device_binding, agent_id)
        transaction.commit()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        try:
            transaction.rollback()
            files_rolled_back = True
        except OSError:
            files_rolled_back = False
        runtime_rolled_back = True
        if files_rolled_back and prepare_started and runtime_snapshot is not None:
            try:
                restore_runtime_state(runtime_snapshot)
            except (OSError, RuntimeError, subprocess.CalledProcessError):
                runtime_rolled_back = False
        rolled_back = files_rolled_back and runtime_rolled_back
        return emit("error", "install_or_deployment_failed", backup=str(transaction.backup) if transaction.backup else None,
                    rolled_back=rolled_back, runtime_rolled_back=runtime_rolled_back,
                    model_called=False, agent_called=False, paid=False)
    return emit("success", "installed_and_healthy", version="2.0.3", backup=str(transaction.backup) if transaction.backup else None,
                model_called=False, agent_called=False, paid=False)


if __name__ == "__main__":
    sys.exit(main())
