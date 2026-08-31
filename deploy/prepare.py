#!/usr/bin/env python3
"""Prepare a bound, pinned Docker deployment without printing secret values."""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = pathlib.Path(__file__).with_name("compose.yaml")
SECRETS = pathlib.Path(__file__).with_name("secrets")
BINDINGS = pathlib.Path(__file__).with_name("bindings")
PROFILE_ROOT = pathlib.Path(__file__).with_name("agent-zero-profile")
PROFILE_ID = "seo_employee_no_tools"
PROFILE_SCHEMA = "extella.agent_zero_no_tools_profile.v1"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
AGENT_RE = re.compile(r"^agent_[A-Za-z0-9_][A-Za-z0-9_-]{2,127}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
AGENT_ZERO_IMAGE = "agent0ai/agent-zero@sha256:9b65805d59b3dab7e14a5e732f6738621546070ec847441da2e75c368adaae30"
PROFILE_FILES = ("plugins/_tool_access/config.json", "plugins/_skills/config.json")

def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""

def atomic_write(path: pathlib.Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

def write_json(path: pathlib.Path, value: object) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

def write_secret(path: pathlib.Path, value: str) -> None:
    value = value.strip()
    if not value or "\n" in value or "\r" in value:
        raise RuntimeError("secret value is invalid")
    atomic_write(path, (value + "\n").encode("utf-8"), 0o600)

def ensure_generated_secret(name: str, minimum: int = 32) -> None:
    path = SECRETS / name
    if path.exists():
        if path.is_symlink() or not path.is_file() or len(path.read_text(encoding="utf-8").strip()) < minimum:
            raise RuntimeError("secret file is invalid")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return
    write_secret(path, secrets.token_urlsafe(32))

def copy_external_token(source: pathlib.Path) -> None:
    source = source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > 4096:
        raise RuntimeError("external Agent Zero token file is invalid")
    token = source.read_text(encoding="utf-8").strip()
    if not TOKEN_RE.fullmatch(token):
        raise RuntimeError("external Agent Zero token is invalid")
    write_secret(SECRETS / "agent_zero_api_key", token)

def profile_assertion() -> dict[str, object]:
    return {"schema": PROFILE_SCHEMA, "agent_profile": PROFILE_ID, "tool_policy": {"mode": "custom", "default": "block", "mcp_default": "block", "allowed": [], "blocked": []}, "skill_policy": {"mode": "custom", "default": "block", "allowed": [], "blocked": []}}

def write_bindings(device_id: str, hosting_profile: str, host: str, agent_id: str) -> None:
    if not DEVICE_RE.fullmatch(device_id): raise RuntimeError("device id is invalid")
    if hosting_profile not in {"local", "server", "client_server"}: raise RuntimeError("hosting profile is invalid")
    if not HOST_RE.fullmatch(host): raise RuntimeError("host is invalid")
    if not AGENT_RE.fullmatch(agent_id): raise RuntimeError("Extella agent id is invalid")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(BINDINGS / "device_binding.json", {"device_id": device_id, "host": host, "hosting_profile": hosting_profile, "since": since})
    write_json(BINDINGS / "agent_binding.json", {"agent_id": agent_id})

def provision_no_tools_profile(container: str) -> None:
    if not CONTAINER_RE.fullmatch(container): raise RuntimeError("Agent Zero container name is invalid")
    for relative in PROFILE_FILES:
        source = PROFILE_ROOT / "usr" / "agents" / PROFILE_ID / relative
        if not source.is_file(): raise RuntimeError(f"missing no-tools profile asset: {relative}")
        destination = f"/a0/usr/agents/{PROFILE_ID}/{relative}"
        run("docker", "exec", container, "mkdir", "-p", destination.rsplit("/", 1)[0])
        run("docker", "cp", str(source), f"{container}:{destination}")
        actual = subprocess.run(["docker", "exec", container, "cat", destination], check=True, capture_output=True).stdout
        if actual != source.read_bytes(): raise RuntimeError(f"Agent Zero no-tools profile byte verification failed: {relative}")
    write_json(BINDINGS / "agent_zero_no_tools_profile.json", profile_assertion())

def sync_managed_token() -> None:
    compose = ("docker", "compose", "-f", str(COMPOSE), "--profile", "managed-agent-zero")
    run(*compose, "up", "-d", "agent-zero")
    container = run(*compose, "ps", "-q", "agent-zero", capture=True)
    if not container: raise RuntimeError("managed Agent Zero container is unavailable")
    provision_no_tools_profile(container)
    command = (*compose, "exec", "-T", "agent-zero", "python", "-c", "from helpers.settings import create_auth_token; print(create_auth_token())")
    for _ in range(45):
        result = subprocess.run(command, text=True, capture_output=True)
        token = result.stdout.strip()
        if result.returncode == 0 and TOKEN_RE.fullmatch(token):
            write_secret(SECRETS / "agent_zero_api_key", token)
            return
        time.sleep(2)
    raise RuntimeError("Agent Zero API token could not be synchronized")

def connect_external_agent_zero(container: str) -> None:
    if not CONTAINER_RE.fullmatch(container): raise RuntimeError("external Agent Zero container name is invalid")
    image = run("docker", "inspect", container, "--format", "{{.Config.Image}}", capture=True)
    running = run("docker", "inspect", container, "--format", "{{.State.Running}}", capture=True)
    if image != AGENT_ZERO_IMAGE or running != "true": raise RuntimeError("external Agent Zero container is not the running pinned release")
    provision_no_tools_profile(container)
    compose = ("docker", "compose", "-f", str(COMPOSE))
    run(*compose, "create", "agent-zero-proxy")
    config = json.loads(run(*compose, "config", "--format", "json", capture=True))
    network = config["networks"]["control"]["name"]
    attached = json.loads(run("docker", "inspect", container, "--format", "{{json .NetworkSettings.Networks}}", capture=True))
    if network not in attached: run("docker", "network", "connect", "--alias", "agent-zero", network, container)
    elif "agent-zero" not in (attached[network].get("Aliases") or []): raise RuntimeError("external Agent Zero is attached without the required network alias")


def loopback_product_url() -> str:
    value = os.environ.get("SEO_EMPLOYEE_PORT", "8088")
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise RuntimeError("SEO_EMPLOYEE_PORT is invalid")
    return f"http://127.0.0.1:{value}/health"


def wait_for_product_health(seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    url = loopback_product_url()
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.HTTPError):
            pass
        time.sleep(1)
    raise RuntimeError("product health endpoint did not become ready after restart")


def start_and_verify() -> None:
    compose = ("docker", "compose", "-f", str(COMPOSE))
    run(*compose, "up", "-d")
    run(*compose, "restart", "seo-employee", "api-gateway")
    wait_for_product_health()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--hosting-profile", required=True, choices=("local", "server", "client_server"))
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--external-agent-zero-key", type=pathlib.Path)
    parser.add_argument("--external-agent-zero-container")
    args = parser.parse_args()
    try:
        if bool(args.external_agent_zero_key) != bool(args.external_agent_zero_container): raise RuntimeError("external Agent Zero requires both key file and container name")
        (BINDINGS / "agent_zero_no_tools_profile.json").unlink(missing_ok=True)
        for command in ("docker", "git"):
            if shutil.which(command) is None: raise RuntimeError(f"missing executable: {command}")
        write_bindings(args.device_id, args.hosting_profile, args.host, args.agent_id)
        run("docker", "compose", "version")
        ensure_generated_secret("crawlseo_db_password")
        ensure_generated_secret("seo_employee_api_token")
        ensure_generated_secret("agent_zero_api_key", 16)
        run(sys.executable, str(ROOT / "tools" / "build_runtime.py"))
        run("docker", "compose", "-f", str(COMPOSE), "build", "seo-employee")
        if args.external_agent_zero_key:
            copy_external_token(args.external_agent_zero_key)
            connect_external_agent_zero(args.external_agent_zero_container)
            mode = "external-container"
        else:
            sync_managed_token(); mode = "managed"
        run("docker", "compose", "-f", str(COMPOSE), "config", "-q")
        start_and_verify()
    except (OSError, RuntimeError, subprocess.CalledProcessError, UnicodeError, KeyError, json.JSONDecodeError):
        print(json.dumps({"status": "error", "code": "deployment_preparation_failed"})); return 1
    print(json.dumps({"status": "success", "agent_zero": mode, "compose": str(COMPOSE)})); return 0

if __name__ == "__main__":
    raise SystemExit(main())
