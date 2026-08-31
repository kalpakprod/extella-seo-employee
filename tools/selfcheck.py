#!/usr/bin/env python3
"""Structural self-check derived from the panel's actual Expert calls."""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    failures: list[str] = []
    app_js = ROOT / "app" / "app.js"
    bridge_js = ROOT / "app" / "extella-bridge.js"
    required_paths = (
        ROOT / "app" / "index.html",
        ROOT / "app" / "styles.css",
        app_js,
        bridge_js,
        ROOT / "runtime" / "container" / "run_crawlseo",
        ROOT / "runtime" / "container" / "run_seomator",
        ROOT / "runtime" / "product" / "Dockerfile",
        ROOT / "runtime" / "product" / "gateway.py",
        ROOT / "runtime" / "worker_server.mjs",
        ROOT / "deploy" / "compose.yaml",
        ROOT / "deploy" / "prepare.py",
        ROOT / "listing.json",
        ROOT / "automation_passport.yaml",
    )
    for path in required_paths:
        if not path.is_file() or path.stat().st_size == 0:
            failures.append(f"missing release file: {path.relative_to(ROOT)}")

    if not failures:
        app_source = app_js.read_text(encoding="utf-8")
        bridge_source = bridge_js.read_text(encoding="utf-8")
        called = set(re.findall(r"const\s+EXPERT_[A-Z_]+\s*=\s*['\"]([a-z0-9_]+)['\"]", app_source))
        if not called:
            failures.append("the panel does not declare any Expert route")
        for name in sorted(called):
            module = ROOT / "experts" / f"{name}.py"
            if not module.is_file():
                failures.append(f"panel Expert is missing: {name}")
                continue
            try:
                tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            except SyntaxError as error:
                failures.append(f"Expert does not compile: {name}: {error.msg}")
                continue
            functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if name not in functions:
                failures.append(f"Expert entrypoint is missing: {name}")
        if "etb_run_expert" not in bridge_source or "etb_expert_result" not in bridge_source:
            failures.append("the panel is not using the Extella message bridge")
        if len(re.findall(r"\bfetch\s*\(", app_source + bridge_source)) > 1:
            failures.append("the panel contains an unexpected direct network request")
        compose = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
        if "docker.sock" in compose:
            failures.append("the deployment mounts the Docker socket")
        if '"127.0.0.1:${SEO_EMPLOYEE_PORT:-8088}:8080"' not in compose:
            failures.append("the product API is not loopback-bound")

    release = ROOT / "release-manifest.json"
    if release.is_file():
        try:
            manifest = json.loads(release.read_text(encoding="utf-8"))
            files = manifest["files"]
            if manifest["schema"] != "extella.seo_employee_release.v1" or not isinstance(files, dict):
                raise ValueError("unsupported schema")
            for relative, expected in files.items():
                path = ROOT / relative
                if not path.is_file() or digest(path) != expected:
                    failures.append(f"release integrity mismatch: {relative}")
        except (KeyError, ValueError, json.JSONDecodeError) as error:
            failures.append(f"release manifest cannot be read: {error}")

    if failures:
        print("NOT READY: Extella SEO Employee self-check failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("READY: the panel routes, Expert entrypoints, runtime wrappers and release files agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
