#!/usr/bin/env python3
"""Build the two pinned, read-only SEO source images."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"

SOURCES = {
    "crawlseo": (
        "https://github.com/crawlseo/crawlseo.git",
        "8683b2740eca5059faa0949c2175a7548216bd50",
    ),
    "seo-audit-skill": (
        "https://github.com/seo-skills/seo-audit-skill.git",
        "bbca017b56086a2959382d8260b97021736ca18f",
    ),
}


def run(*args: str, cwd: pathlib.Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def source(name: str) -> pathlib.Path:
    repository, commit = SOURCES[name]
    candidates = (VENDOR / name, VENDOR / f"{name}-{commit[:12]}")
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        try:
            if run("git", "-C", str(candidate), "rev-parse", "HEAD") == commit:
                return candidate
        except subprocess.CalledProcessError:
            continue

    destination = candidates[1]
    VENDOR.mkdir(parents=True, exist_ok=True)
    temporary = VENDOR / f".{destination.name}.tmp"
    if temporary.exists():
        shutil.rmtree(temporary)
    try:
        run("git", "clone", "--filter=blob:none", repository, str(temporary))
        run("git", "-C", str(temporary), "checkout", "--detach", commit)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def stage(source_root: pathlib.Path, destination: pathlib.Path) -> None:
    shutil.copytree(
        source_root,
        destination,
        ignore=shutil.ignore_patterns(".git", "node_modules", "dist", "coverage"),
    )


def build_crawlseo(temporary_root: pathlib.Path) -> None:
    context = temporary_root / "crawlseo"
    stage(source("crawlseo"), context)
    run("git", "apply", "--check", str(ROOT / "patches" / "crawlseo-one-page-cap.patch"), cwd=context)
    run("git", "apply", str(ROOT / "patches" / "crawlseo-one-page-cap.patch"), cwd=context)
    shutil.copy2(ROOT / "runtime" / "crawlseo" / "Dockerfile", context / "Dockerfile")
    shutil.copy2(ROOT / "runtime" / "crawlseo" / "entrypoint.mjs", context / "extella_entrypoint.mjs")
    shutil.copy2(ROOT / "runtime" / "worker_server.mjs", context / "extella_worker_server.mjs")
    shutil.copy2(ROOT / "runtime" / "safe_fetch.mjs", context / "extella_safe_fetch.mjs")
    shutil.copy2(ROOT / "tools" / "crawlseo_once.mjs", context / "crawlseo_once.mjs")
    shutil.copytree(ROOT / "runtime" / "crawlseo" / "runner", context / "runner")
    subprocess.run(
        [
            "docker", "build", "--pull=false", "--tag", "extella-seo-crawlseo:2.0.0",
            "--label", "org.extella.product=seo-employee",
            "--label", f"org.extella.source.commit={SOURCES['crawlseo'][1]}", ".",
        ],
        cwd=context,
        check=True,
    )


def build_seomator(temporary_root: pathlib.Path) -> None:
    context = temporary_root / "seomator"
    stage(source("seo-audit-skill"), context)
    run("git", "apply", "--check", str(ROOT / "patches" / "seomator-ssrf-guard.patch"), cwd=context)
    run("git", "apply", str(ROOT / "patches" / "seomator-ssrf-guard.patch"), cwd=context)
    run("git", "apply", "--check", str(ROOT / "patches" / "seomator-external-link-budget.patch"), cwd=context)
    run("git", "apply", str(ROOT / "patches" / "seomator-external-link-budget.patch"), cwd=context)
    shutil.copy2(ROOT / "runtime" / "seomator" / "Dockerfile", context / "Dockerfile")
    shutil.copy2(
        ROOT / "runtime" / "seomator" / "browser_route.mjs",
        context / "extella_browser_route.mjs",
    )
    shutil.copy2(
        ROOT / "runtime" / "seomator" / "normalize-package.mjs",
        context / "extella_normalize_package.mjs",
    )
    shutil.copy2(ROOT / "runtime" / "seomator" / "entrypoint.mjs", context / "extella_entrypoint.mjs")
    shutil.copy2(ROOT / "runtime" / "worker_server.mjs", context / "extella_worker_server.mjs")
    shutil.copy2(ROOT / "runtime" / "safe_fetch.mjs", context / "extella_safe_fetch.mjs")
    subprocess.run(
        [
            "docker", "build", "--pull=false", "--tag", "extella-seo-seomator:2.0.0",
            "--label", "org.extella.product=seo-employee",
            "--label", f"org.extella.source.commit={SOURCES['seo-audit-skill'][1]}", ".",
        ],
        cwd=context,
        check=True,
    )


def main() -> int:
    for command in ("git", "docker"):
        if shutil.which(command) is None:
            print(json.dumps({"status": "error", "code": f"missing_{command}"}))
            return 2
    try:
        with tempfile.TemporaryDirectory(prefix="extella-seo-build-") as directory:
            temporary_root = pathlib.Path(directory)
            build_crawlseo(temporary_root)
            build_seomator(temporary_root)
        images = {
            name: run("docker", "image", "inspect", tag, "--format", "{{.Id}}")
            for name, tag in {
                "crawlseo": "extella-seo-crawlseo:2.0.0",
                "seomator": "extella-seo-seomator:2.0.0",
            }.items()
        }
    except (OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "error", "code": "runtime_build_failed", "detail": str(error)}))
        return 1
    print(json.dumps({"status": "success", "images": images}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
