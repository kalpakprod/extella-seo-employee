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
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
IMAGE_ID_RE = re.compile(r"^(?:sha256:)?[a-f0-9]{12,64}$")
IMAGE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,255}$")
LOOPBACK_HEALTH_RE = re.compile(r"^http://(127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})/health$")
AGENT_ZERO_IMAGE = "agent0ai/agent-zero@sha256:9b65805d59b3dab7e14a5e732f6738621546070ec847441da2e75c368adaae30"
PROFILE_FILES = ("plugins/_tool_access/config.json", "plugins/_skills/config.json")
RUNTIME_STATE_SCHEMA = "extella.seo_employee_runtime_state.v1"

def run(*args: str, capture: bool = False, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=capture, env=env)
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

def sync_managed_token() -> str:
    compose = ("docker", "compose", "-f", str(COMPOSE), "--profile", "managed-agent-zero")
    run(*compose, "up", "-d", "agent-zero")
    container = run(*compose, "ps", "-q", "agent-zero", capture=True)
    if not container: raise RuntimeError("managed Agent Zero container is unavailable")
    command = (*compose, "exec", "-T", "agent-zero", "python", "-c", "from helpers.settings import create_auth_token; print(create_auth_token())")
    for _ in range(45):
        result = subprocess.run(command, text=True, capture_output=True)
        token = result.stdout.strip()
        if result.returncode == 0 and TOKEN_RE.fullmatch(token):
            write_secret(SECRETS / "agent_zero_api_key", token)
            return container
        time.sleep(2)
    raise RuntimeError("Agent Zero API token could not be synchronized")

def connect_external_agent_zero(container: str) -> str:
    if not CONTAINER_RE.fullmatch(container): raise RuntimeError("external Agent Zero container name is invalid")
    image = run("docker", "inspect", container, "--format", "{{.Config.Image}}", capture=True)
    running = run("docker", "inspect", container, "--format", "{{.State.Running}}", capture=True)
    if image != AGENT_ZERO_IMAGE or running != "true": raise RuntimeError("external Agent Zero container is not the running pinned release")
    compose = ("docker", "compose", "-f", str(COMPOSE))
    run(*compose, "create", "agent-zero-proxy")
    config = json.loads(run(*compose, "config", "--format", "json", capture=True))
    network = config["networks"]["control"]["name"]
    attached = json.loads(run("docker", "inspect", container, "--format", "{{json .NetworkSettings.Networks}}", capture=True))
    if network not in attached: run("docker", "network", "connect", "--alias", "agent-zero", network, container)
    elif "agent-zero" not in (attached[network].get("Aliases") or []): raise RuntimeError("external Agent Zero is attached without the required network alias")
    return container


def compose_command(compose: pathlib.Path, project: str | None = None) -> tuple[str, ...]:
    command = ("docker", "compose")
    if project is not None:
        command += ("--project-name", project)
    return (*command, "-f", str(compose))


def validate_loopback_health_url(url: object) -> str:
    if not isinstance(url, str):
        raise RuntimeError("runtime loopback health URL is invalid")
    match = LOOPBACK_HEALTH_RE.fullmatch(url)
    if match is None or not 1 <= int(match.group(2)) <= 65535:
        raise RuntimeError("runtime loopback health URL is invalid")
    return url


def container_loopback_health_url(container: str) -> str:
    ports = json.loads(run("docker", "inspect", container, "--format", "{{json .NetworkSettings.Ports}}", capture=True))
    if not isinstance(ports, dict):
        raise RuntimeError("existing API gateway ports are invalid")
    bindings = ports.get("8080/tcp")
    if not isinstance(bindings, list) or len(bindings) != 1 or not isinstance(bindings[0], dict):
        raise RuntimeError("existing API gateway loopback binding is unavailable")
    host, port = bindings[0].get("HostIp"), bindings[0].get("HostPort")
    if host == "127.0.0.1" and isinstance(port, str) and port.isdecimal():
        return validate_loopback_health_url(f"http://127.0.0.1:{port}/health")
    if host == "::1" and isinstance(port, str) and port.isdecimal():
        return validate_loopback_health_url(f"http://[::1]:{port}/health")
    raise RuntimeError("existing API gateway loopback binding is unavailable")


def capture_runtime_state(compose: pathlib.Path) -> dict[str, object] | None:
    if not compose.is_file():
        return None
    containers = tuple(value for value in run(*compose_command(compose), "ps", "-aq", capture=True).splitlines() if value)
    if not containers:
        return None
    project: str | None = None
    health_url: str | None = None
    images: dict[tuple[str, str], dict[str, str]] = {}
    for container in containers:
        labels = json.loads(run("docker", "inspect", container, "--format", "{{json .Config.Labels}}", capture=True))
        if not isinstance(labels, dict):
            raise RuntimeError("existing Compose container has invalid labels")
        current_project = labels.get("com.docker.compose.project")
        if not isinstance(current_project, str) or not PROJECT_RE.fullmatch(current_project):
            raise RuntimeError("existing Compose project identity is invalid")
        if project is None:
            project = current_project
        elif project != current_project:
            raise RuntimeError("existing Compose containers have different project identities")
        if labels.get("com.docker.compose.service") == "api-gateway":
            if health_url is not None:
                raise RuntimeError("existing Compose state has multiple API gateways")
            health_url = container_loopback_health_url(container)
        reference = run("docker", "inspect", container, "--format", "{{.Config.Image}}", capture=True)
        image_id = run("docker", "inspect", container, "--format", "{{.Image}}", capture=True)
        if not IMAGE_REFERENCE_RE.fullmatch(reference) or not IMAGE_ID_RE.fullmatch(image_id):
            raise RuntimeError("existing Compose image state is invalid")
        images[(reference, image_id)] = {"reference": reference, "image_id": image_id}
    if project is None or health_url is None:
        raise RuntimeError("existing Compose runtime health is unavailable")
    return {"schema": RUNTIME_STATE_SCHEMA, "project": project, "images": list(images.values()), "health_url": health_url}


def restore_runtime_state(state: object, compose: pathlib.Path) -> None:
    if state is None:
        return
    if not compose.is_file() or not isinstance(state, dict) or state.get("schema") != RUNTIME_STATE_SCHEMA:
        raise RuntimeError("runtime rollback state is invalid")
    project, images, health_url = state.get("project"), state.get("images"), state.get("health_url")
    if not isinstance(project, str) or not PROJECT_RE.fullmatch(project) or not isinstance(images, list) or not images:
        raise RuntimeError("runtime rollback state is invalid")
    health_url = validate_loopback_health_url(health_url)
    health_match = LOOPBACK_HEALTH_RE.fullmatch(health_url)
    if health_match is None:
        raise RuntimeError("runtime loopback health URL is invalid")
    for image in images:
        if not isinstance(image, dict):
            raise RuntimeError("runtime rollback image state is invalid")
        reference, image_id = image.get("reference"), image.get("image_id")
        if not isinstance(reference, str) or not isinstance(image_id, str) or not IMAGE_REFERENCE_RE.fullmatch(reference) or not IMAGE_ID_RE.fullmatch(image_id):
            raise RuntimeError("runtime rollback image state is invalid")
        if "@sha256:" not in reference:
            run("docker", "tag", image_id, reference)
    environment = os.environ.copy()
    environment["SEO_EMPLOYEE_PORT"] = health_match.group(2)
    run(*compose_command(compose, project), "up", "-d", env=environment)
    wait_for_product_health(url=health_url)


def loopback_product_url() -> str:
    value = os.environ.get("SEO_EMPLOYEE_PORT", "8088")
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise RuntimeError("SEO_EMPLOYEE_PORT is invalid")
    return f"http://127.0.0.1:{value}/health"


def wait_for_product_health(seconds: int = 120, url: str | None = None) -> None:
    deadline = time.monotonic() + seconds
    url = loopback_product_url() if url is None else validate_loopback_health_url(url)
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
    parser.add_argument("--device-id")
    parser.add_argument("--hosting-profile", choices=("local", "server", "client_server"))
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--agent-id")
    parser.add_argument("--external-agent-zero-key", type=pathlib.Path)
    parser.add_argument("--external-agent-zero-container")
    parser.add_argument("--capture-runtime-state", action="store_true")
    parser.add_argument("--restore-runtime-state", action="store_true")
    parser.add_argument("--compose-file", type=pathlib.Path)
    parser.add_argument("--runtime-state", type=pathlib.Path)
    args = parser.parse_args()
    if args.capture_runtime_state or args.restore_runtime_state:
        if args.capture_runtime_state == args.restore_runtime_state or args.compose_file is None or args.runtime_state is None:
            parser.error("runtime state action requires exactly one action, --compose-file, and --runtime-state")
        try:
            if args.capture_runtime_state:
                write_json(args.runtime_state, capture_runtime_state(args.compose_file))
            else:
                restore_runtime_state(json.loads(args.runtime_state.read_text(encoding="utf-8")), args.compose_file)
        except (OSError, RuntimeError, subprocess.CalledProcessError, UnicodeError, json.JSONDecodeError):
            print(json.dumps({"status": "error", "code": "runtime_rollback_failed"})); return 1
        return 0
    if args.device_id is None or args.hosting_profile is None or args.agent_id is None:
        parser.error("--device-id, --hosting-profile, and --agent-id are required for deployment preparation")
    try:
        if bool(args.external_agent_zero_key) != bool(args.external_agent_zero_container): raise RuntimeError("external Agent Zero requires both key file and container name")
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
            container = connect_external_agent_zero(args.external_agent_zero_container)
            mode = "external-container"
        else:
            container = sync_managed_token(); mode = "managed"
        run("docker", "compose", "-f", str(COMPOSE), "config", "-q")
        start_and_verify()
        provision_no_tools_profile(container)
    except (OSError, RuntimeError, subprocess.CalledProcessError, UnicodeError, KeyError, json.JSONDecodeError):
        print(json.dumps({"status": "error", "code": "deployment_preparation_failed"})); return 1
    print(json.dumps({"status": "success", "agent_zero": mode, "compose": str(COMPOSE)})); return 0

if __name__ == "__main__":
    raise SystemExit(main())
