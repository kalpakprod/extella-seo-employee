#!/usr/bin/env python3
"""Build deterministic Extella page and CT160 runtime archives."""

from __future__ import annotations

import hashlib
import json
import pathlib
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERSION = "2.0.0"
EPOCH = (2026, 1, 1, 0, 0, 0)
APP_FILES = ("index.html", "styles.css", "app.js", "extella-bridge.js")
ROOT_FILES = (
    ".dockerignore",
    "MANIFEST.yaml",
    "README.md",
    "automation_passport.yaml",
    "icon.png",
    "icon.svg",
    "listing.json",
    "install.py",
    "manifest_check.py",
)
PAYLOAD_DIRS = ("app", "deploy", "docs", "experts", "patches", "runtime", "tests", "tools")
EXECUTABLES = {
    "deploy/prepare.py",
    "deploy/probe.py",
    "runtime/container/run_crawlseo",
    "runtime/container/run_seomator",
}
LEGACY_RUNTIME = {
    "runtime/run_crawlseo",
    "runtime/run_seomator",
    "runtime/systemd/extella-seo-employee-schedule.service",
    "runtime/systemd/extella-seo-employee-schedule.timer",
}


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload_files() -> list[str]:
    files = list(ROOT_FILES)
    for directory in PAYLOAD_DIRS:
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in LEGACY_RUNTIME:
                continue
            if relative.startswith("docs/investor/"):
                continue
            if relative.startswith("docs/plans/") or relative == "docs/verification/v2-progress.md":
                continue
            if relative.startswith("deploy/secrets/") and relative != "deploy/secrets/.gitignore":
                continue
            if relative.startswith("deploy/bindings/") and relative != "deploy/bindings/.gitignore":
                continue
            if relative == "deploy/.env":
                continue
            files.append(relative)
    return sorted(set(files))


def write_zip(output: pathlib.Path, root: pathlib.Path, files: list[str], executables: set[str]) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(files):
            path = root / relative
            if not path.is_file():
                raise FileNotFoundError(relative)
            info = zipfile.ZipInfo(relative, date_time=EPOCH)
            info.create_system = 3
            info.external_attr = (0o755 if relative in executables else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return sha256(output)


def main() -> int:
    payload = payload_files()
    manifest = {
        "schema": "extella.seo_employee_release.v1",
        "version": VERSION,
        "files": {relative: sha256(ROOT / relative) for relative in payload},
    }
    (ROOT / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    page_name = f"extella-seo-employee-page-{VERSION}.zip"
    runtime_name = f"extella-seo-employee-runtime-{VERSION}.zip"
    page_hash = write_zip(DIST / page_name, ROOT / "app", list(APP_FILES), set())
    runtime_hash = write_zip(
        DIST / runtime_name,
        ROOT,
        payload + ["release-manifest.json"],
        EXECUTABLES,
    )
    result = {
        "status": "success",
        "version": VERSION,
        "artifacts": {
            "page": {"file": page_name, "sha256": page_hash},
            "runtime": {"file": runtime_name, "sha256": runtime_hash},
        },
        "payload_files": len(payload),
    }
    (DIST / "build.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
