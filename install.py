#!/usr/bin/env python3
"""Install the verified payload for Extella SEO Employee without starting services."""
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
    shutil.copy2(HERE / "release-manifest.json", TARGET / "release-manifest.json")
    if copied_previous:
        (backup / "rollback.json").write_text(json.dumps({"target": str(TARGET), "release": "2.0.0"}, indent=2) + "\n", encoding="utf-8")
        return backup
    return None


def write_agent_binding(agent_id: str) -> None:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise RuntimeError("EXTELLA_AGENT_ID is not a canonical Extella agent id")
    binding = TARGET / "deploy" / "bindings" / "agent_binding.json"
    binding.parent.mkdir(parents=True, exist_ok=True)
    temporary = binding.with_name(f".{binding.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"agent_id": agent_id}, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    os.replace(temporary, binding)


def main() -> int:
    os.chdir(HERE)
    if not run_manifest_check(HERE / "MANIFEST.yaml"):
        return emit("error", "manifest_check_failed", model_called=False, agent_called=False, paid=False)
    agent_id = os.environ.get("EXTELLA_AGENT_ID", "").strip()
    if not AGENT_ID_RE.fullmatch(agent_id):
        return emit("error", "extella_agent_id_required", model_called=False, agent_called=False, paid=False)
    if os.name != "posix":
        return emit("error", "linux_required", model_called=False, agent_called=False, paid=False)
    if shutil.which("docker") is None:
        return emit("error", "missing_docker", model_called=False, agent_called=False, paid=False)
    try:
        subprocess.run(("docker", "info"), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        backup = install_payload(release_manifest(HERE))
        write_agent_binding(agent_id)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        return emit("error", "install_failed", detail=str(error), model_called=False, agent_called=False, paid=False)
    return emit("success", "payload_installed", version="2.0.0", backup=str(backup) if backup else None,
                next_step="run deploy/prepare.py with --device-id, --hosting-profile and --agent-id",
                model_called=False, agent_called=False, paid=False)


if __name__ == "__main__":
    sys.exit(main())
