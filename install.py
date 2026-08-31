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


def install_payload(files: dict[str, str]) -> pathlib.Path | None:
    if HERE == TARGET:
        return None
    backup = BACKUPS / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    copied_previous = False
    for relative in sorted(files):
        source, destination = HERE / relative, TARGET / relative
        if destination.is_file():
            previous = backup / relative
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, previous)
            copied_previous = True
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    manifest_destination = TARGET / "release-manifest.json"
    if manifest_destination.is_file():
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_destination, backup / "release-manifest.json")
        copied_previous = True
    shutil.copy2(HERE / "release-manifest.json", manifest_destination)
    if copied_previous:
        (backup / "rollback.json").write_text(json.dumps({"target": str(TARGET), "release": "2.0.1"}, indent=2) + "\n", encoding="utf-8")
        return backup
    return None


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


def write_agent_binding(agent_id: str, backup: pathlib.Path | None) -> None:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise RuntimeError("EXTELLA_AGENT_ID is not a canonical Extella agent id")
    binding = TARGET / "deploy" / "bindings" / "agent_binding.json"
    binding.parent.mkdir(parents=True, exist_ok=True)
    if backup is not None and binding.is_file():
        previous = backup / "deploy" / "bindings" / "agent_binding.json"
        previous.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binding, previous)
    temporary = binding.with_name(f".{binding.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"agent_id": agent_id}, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    os.replace(temporary, binding)


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
    try:
        subprocess.run(("docker", "info"), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        backup = install_payload(release_manifest(HERE))
        write_agent_binding(agent_id, backup)
        prepare_and_start(device_binding, agent_id)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        return emit("error", "install_or_deployment_failed", backup=str(backup) if "backup" in locals() and backup else None,
                    model_called=False, agent_called=False, paid=False)
    return emit("success", "installed_and_healthy", version="2.0.1", backup=str(backup) if backup else None,
                model_called=False, agent_called=False, paid=False)


if __name__ == "__main__":
    sys.exit(main())
