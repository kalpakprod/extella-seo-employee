#!/usr/bin/env python3
"""Copy root-readable Compose secrets to tmpfs, then permanently drop privileges."""

from __future__ import annotations

import os
import pathlib
import sys


APP_UID = 10001
APP_GID = 10001
SERVER = str(pathlib.Path(__file__).with_name("server.py"))


def read_secret(path: pathlib.Path, minimum: int) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < minimum or "\n" in value or "\r" in value:
        raise RuntimeError("deployment secret is invalid")
    return value


def main() -> int:
    api_token = read_secret(pathlib.Path("/run/secrets/seo_employee_api_token"), 32)
    agent_zero_token = read_secret(pathlib.Path("/run/secrets/agent_zero_api_key"), 16)
    os.setgroups([])
    os.setgid(APP_GID)
    os.setuid(APP_UID)
    os.umask(0o077)
    target = pathlib.Path("/tmp/extella-secrets")
    target.mkdir(mode=0o700)
    api_path = target / "seo_employee_api_token"
    agent_path = target / "agent_zero_api_key"
    api_path.write_text(api_token + "\n", encoding="utf-8")
    agent_path.write_text(agent_zero_token + "\n", encoding="utf-8")
    os.environ["EXTELLA_SEO_API_TOKEN_FILE"] = str(api_path)
    os.environ["EXTELLA_AGENT_ZERO_API_KEY_FILE"] = str(agent_path)
    os.execv(sys.executable, [sys.executable, SERVER])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
