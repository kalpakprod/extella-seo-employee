from __future__ import annotations

"""Safe, dependency-free snapshots for the Extella SEO Employee Compose release.

The script deliberately keeps the backup boundary narrow.  It archives only the
five product volumes named below and a logical dump of the CrawlSEO database.
Compose secrets, binding files, the Agent Zero volume, and raw PostgreSQL files
never enter a snapshot.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Sequence


SCHEMA = "extella.seo_employee_backup.v1"
COMPOSE_FILE = Path(__file__).resolve().with_name("compose.yaml")
DEFAULT_BACKUP_DIR = Path(
    os.environ.get(
        "EXTELLA_BACKUP_DIR",
        str(Path.home() / ".local" / "share" / "extella-seo-employee" / "backups"),
    )
)
PRODUCT_VOLUMES = (
    "seo_config",
    "seo_state",
    "seo_reports",
    "seo_history",
    "seo_evidence",
)
DATABASE_SERVICE = "crawlseo-db"
DATABASE_NAME = "crawlseo"
DATABASE_USER = "crawlseo"

# These IDs are part of the manifest contract.  Keeping them explicit makes a
# tampered manifest fail verification instead of silently widening the scope.
EXCLUDED_CATEGORIES = (
    {
        "category": "deploy/secrets",
        "reason": "Compose credentials, API tokens, and password files are excluded",
    },
    {
        "category": "deploy/bindings",
        "reason": "device IDs and agent binding records are excluded",
    },
    {
        "category": "agent_zero_usr",
        "reason": "Agent Zero usr data, provider credentials, and sessions are excluded",
    },
    {
        "category": "crawlseo_db_raw_volume",
        "reason": "raw PostgreSQL volume files are excluded; only a logical dump is included",
    },
)


class BackupError(RuntimeError):
    """A user-actionable backup operation failure."""


ARCHIVE_PYTHON = r'''
import os
import sys
import tarfile

base = "/backup-source"
if os.path.islink(base):
    raise SystemExit("source volume root is a symlink")

with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
    archive.add(base, arcname=".", recursive=False)
    for root, directories, files in os.walk(base, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories:
            path = os.path.join(root, name)
            if os.path.islink(path):
                raise SystemExit("symlink in source volume")
            archive.add(path, arcname=os.path.relpath(path, base), recursive=False)
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                raise SystemExit("symlink in source volume")
            archive.add(path, arcname=os.path.relpath(path, base), recursive=False)
'''


RESTORE_PYTHON = r'''
import os
import shutil
from pathlib import Path

source = Path("/restore-source")
target = Path("/restore-target")

def set_product_owner(path):
    try:
        os.chown(path, 10001, 10001)
    except AttributeError:
        pass

def ensure_no_link(path):
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise SystemExit("symlink in restore path")
        current = current.parent

ensure_no_link(target)
target.mkdir(parents=True, exist_ok=True)

def remove_tree(path):
    ensure_no_link(path)
    for child in list(path.iterdir()):
        if child.is_symlink():
            child.unlink()
        elif child.is_dir():
            remove_tree(child)
            child.rmdir()
        else:
            child.unlink()

for child in list(target.iterdir()):
    ensure_no_link(child)
    if child.is_dir() and not child.is_symlink():
        remove_tree(child)
        child.rmdir()
    else:
        child.unlink()

def copy_tree(src, dst):
    ensure_no_link(src)
    ensure_no_link(dst)
    if src.is_symlink():
        raise SystemExit("symlink in restore source")
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        set_product_owner(dst)
        for child in sorted(src.iterdir(), key=lambda item: item.name):
            copy_tree(child, dst / child.name)
        return
    if not src.is_file():
        raise SystemExit("unsupported restore entry")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as source_handle, dst.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle)
    shutil.copymode(src, dst)
    set_product_owner(dst)

for child in sorted(source.iterdir(), key=lambda item: item.name):
    copy_tree(child, target / child.name)
set_product_owner(target)
'''


def _command_text(args: Sequence[str]) -> str:
    """Return a safe command description without process output or secrets."""

    if not args:
        return "command"
    return " ".join(str(item) for item in args[:4])


def _run(
    args: Sequence[str],
    *,
    stdin: Any = None,
    stdout: Any = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a Docker command while keeping its output out of normal logs."""

    capture = stdout is None
    try:
        result = subprocess.run(
            list(args),
            stdin=stdin,
            stdout=subprocess.PIPE if capture else stdout,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise BackupError(f"unable to run {_command_text(args)}") from exc
    if result.returncode != 0:
        raise BackupError(f"command failed: {_command_text(args)}")
    return result


def _run_stream(args: Sequence[str], destination: Path) -> None:
    _ensure_directory(destination.parent)
    try:
        with destination.open("wb") as handle:
            _run(args, stdout=handle)
    except OSError as exc:
        raise BackupError(f"unable to write backup artifact {destination.name}") from exc
    os.chmod(destination, 0o600)


def _run_stdin_file(args: Sequence[str], source: Path) -> None:
    try:
        with source.open("rb") as handle:
            _run(args, stdin=handle)
    except OSError as exc:
        raise BackupError(f"unable to read backup artifact {source.name}") from exc


def _compose_args(
    compose_file: Path,
    *parts: str,
    project_name: str | None = None,
) -> list[str]:
    command = ["docker", "compose"]
    if project_name is not None:
        if (
            not project_name
            or not project_name[0].isalnum()
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in project_name)
        ):
            raise BackupError("Compose project name is invalid")
        command.extend(["--project-name", project_name])
    return [*command, "-f", str(compose_file), *parts]


def _stdout_text(result: subprocess.CompletedProcess[bytes]) -> str:
    output = result.stdout
    if isinstance(output, str):
        return output
    if isinstance(output, bytes):
        try:
            return output.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BackupError("command output is not valid UTF-8") from exc
    raise BackupError("command output is missing")


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _secure_backup_root(path: Path, *, create: bool) -> Path:
    path = path.expanduser()
    current = path.absolute()
    while current != current.parent:
        if current.is_symlink():
            raise BackupError("backup path contains a symlink")
        current = current.parent
    if path.exists() and path.is_symlink():
        raise BackupError("backup directory must not be a symlink")
    if create:
        _ensure_directory(path)
    elif not path.is_dir():
        raise BackupError("backup directory does not exist")
    _reject_symlink_components(path, path)
    return path


def _reject_symlink_components(root: Path, path: Path) -> None:
    """Reject symlinks between a trusted root and a candidate path."""

    root = root.absolute()
    path = path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise BackupError("path escapes the backup directory") from exc
    current = root
    if current.is_symlink():
        raise BackupError("backup path contains a symlink")
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise BackupError("backup path contains a symlink")


def _safe_relative(value: str) -> str:
    """Validate a manifest or tar member path and return POSIX spelling."""

    if not isinstance(value, str) or "\x00" in value:
        raise BackupError("unsafe path in snapshot")
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in ("", "."):
        return ""
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ":" in pure.parts[0]:
        raise BackupError("unsafe path in snapshot")
    if any(part in ("", ".", "..") for part in pure.parts):
        raise BackupError("unsafe path in snapshot")
    return "/".join(pure.parts)


def _path_in(root: Path, relative: str) -> Path:
    safe = _safe_relative(relative)
    if not safe:
        return root
    candidate = root.joinpath(*safe.split("/"))
    _reject_symlink_components(root, candidate)
    try:
        candidate.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise BackupError("path escapes the backup directory") from exc
    return candidate


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise BackupError(f"unable to read backup artifact {path.name}") from exc
    return digest.hexdigest(), size


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BackupError(f"unable to write {path.name}") from exc


def _compose_metadata(
    compose_file: Path,
    *,
    project_name: str | None = None,
) -> tuple[dict[str, str], str]:
    result = _run(_compose_args(compose_file, "config", "--format", "json", project_name=project_name))
    try:
        payload = json.loads(_stdout_text(result))
        raw_volumes = payload["volumes"]
        services = payload["services"]
        product_image = services["seo-employee"]["image"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise BackupError("Compose metadata is invalid") from exc
    if not isinstance(raw_volumes, dict) or not isinstance(product_image, str) or not product_image:
        raise BackupError("Compose metadata is incomplete")
    project = payload.get("name")
    if project_name is not None and project != project_name:
        raise BackupError("Compose metadata project does not match --project-name")
    names: dict[str, str] = {}
    for logical in PRODUCT_VOLUMES:
        definition = raw_volumes.get(logical)
        if not isinstance(definition, dict):
            raise BackupError(f"Compose volume {logical} is missing")
        actual = definition.get("name")
        if not isinstance(actual, str) or not actual:
            if not isinstance(project, str) or not project:
                raise BackupError(f"Compose volume {logical} has no resolved name")
            actual = f"{project}_{logical}"
        if not actual[0].isalnum() or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for character in actual
        ):
            raise BackupError(f"Compose volume {logical} has an unsafe name")
        if actual in names.values():
            raise BackupError("Compose product volume names are not unique")
        names[logical] = actual
    return names, product_image


def _parse_compose_ps(raw: str) -> set[str]:
    if not raw.strip():
        return set()
    records: list[Any] = []
    try:
        decoded = json.loads(raw)
        records = decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                fields = line.split("\t", 1)
                if len(fields) == 2:
                    records.append({"Service": fields[0], "State": fields[1]})
    running: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        service = record.get("Service") or record.get("service")
        state = record.get("State") or record.get("state") or record.get("Status") or record.get("status") or ""
        if isinstance(service, str) and isinstance(state, str) and (
            "running" in state.lower() or state.lower().startswith("up")
        ):
            running.add(service)
    return running


def _running_services(compose_file: Path, *, project_name: str | None = None) -> set[str]:
    result = _run(_compose_args(compose_file, "ps", "-a", "--format", "json", project_name=project_name))
    raw = _stdout_text(result)
    return _parse_compose_ps(raw)


def _stop_non_database(compose_file: Path, running: set[str], *, project_name: str | None = None) -> None:
    services = sorted(running - {DATABASE_SERVICE})
    if services:
        _run(_compose_args(compose_file, "stop", *services, project_name=project_name))


def _start_database_if_needed(
    compose_file: Path,
    running: set[str],
    *,
    project_name: str | None = None,
) -> bool:
    if DATABASE_SERVICE in running:
        return False
    _run(_compose_args(compose_file, "start", DATABASE_SERVICE, project_name=project_name))
    return True


def _stop_database(compose_file: Path, *, project_name: str | None = None) -> None:
    _run(_compose_args(compose_file, "stop", DATABASE_SERVICE, project_name=project_name))


def _restore_service_state(compose_file: Path, running: set[str], *, project_name: str | None = None) -> None:
    if running:
        _run(_compose_args(compose_file, "start", *sorted(running), project_name=project_name))


def _database_dump(compose_file: Path, destination: Path, *, project_name: str | None = None) -> None:
    command = _compose_args(
        compose_file,
        "exec",
        "-T",
        DATABASE_SERVICE,
        "pg_dump",
        "--format=plain",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        f"--username={DATABASE_USER}",
        f"--dbname={DATABASE_NAME}",
        project_name=project_name,
    )
    _run_stream(command, destination)


def _archive_volume(
    logical: str,
    actual: str,
    image: str,
    destination: Path,
) -> None:
    if logical not in PRODUCT_VOLUMES:
        raise BackupError("attempted to archive an unapproved volume")
    # ``docker run -v`` creates a missing volume.  Inspect first so a typo or
    # incomplete deployment cannot silently produce an empty backup.
    _run(["docker", "volume", "inspect", actual])
    mount = f"type=volume,source={actual},target=/backup-source,readonly"
    command = [
        "docker",
        "run",
        "--rm",
        "--mount",
        mount,
        image,
        "python",
        "-c",
        ARCHIVE_PYTHON,
    ]
    _run_stream(command, destination)


def _artifact(path: Path, *, kind: str, name: str, logical_volume: str | None = None) -> dict[str, Any]:
    digest, size = _hash_file(path)
    result: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "path": path.name,
        "sha256": digest,
        "size": size,
    }
    if logical_volume is not None:
        result["volume"] = logical_volume
    return result


def _manifest(
    snapshot_id: str,
    compose_file: Path,
    volume_names: dict[str, str],
    volume_artifacts: list[dict[str, Any]],
    postgres_artifact: dict[str, Any],
) -> dict[str, Any]:
    artifacts = [*volume_artifacts, postgres_artifact]
    exclusions = [dict(item) for item in EXCLUDED_CATEGORIES]
    return {
        "schema": SCHEMA,
        "snapshot_id": snapshot_id,
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "compose_file": compose_file.name,
        "scope": {
            "included_non_sensitive_product_volumes": list(PRODUCT_VOLUMES),
            "postgres": {
                "service": DATABASE_SERVICE,
                "database": DATABASE_NAME,
                "mode": "logical_dump",
            },
            "excluded_non_sensitive_categories": exclusions,
        },
        "excluded_non_sensitive_categories": exclusions,
        "volume_names": dict(volume_names),
        "volumes": volume_artifacts,
        "postgres": [postgres_artifact],
        "artifacts": artifacts,
    }


def create_snapshot(
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    compose_file: Path = COMPOSE_FILE,
    *,
    project_name: str | None = None,
) -> Path:
    """Create one atomic snapshot and restore the initial service state."""

    root = _secure_backup_root(Path(backup_dir), create=True)
    compose_file = Path(compose_file).expanduser()
    volume_names, image = _compose_metadata(compose_file, project_name=project_name)
    running = _running_services(compose_file, project_name=project_name)
    snapshot_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    final = root / snapshot_id
    temporary = root / f".{snapshot_id}.{uuid.uuid4().hex}.tmp"
    if final.exists() or final.is_symlink():
        raise BackupError("snapshot destination already exists")
    _ensure_directory(temporary)
    database_started = False
    database_stopped = False
    operation_error: BaseException | None = None
    try:
        _stop_non_database(compose_file, running, project_name=project_name)
        database_started = _start_database_if_needed(compose_file, running, project_name=project_name)
        postgres_path = temporary / "postgres" / "crawlseo.sql"
        _database_dump(compose_file, postgres_path, project_name=project_name)
        # Freeze the product volumes while they are copied.  The database is
        # already represented by the logical dump and is not copied as files.
        _stop_database(compose_file, project_name=project_name)
        database_stopped = True
        volume_artifacts: list[dict[str, Any]] = []
        for logical in PRODUCT_VOLUMES:
            archive_path = temporary / "volumes" / f"{logical}.tar"
            _archive_volume(logical, volume_names[logical], image, archive_path)
            volume_artifacts.append(
                _artifact(
                    archive_path,
                    kind="volume",
                    name=logical,
                    logical_volume=logical,
                )
                | {"path": f"volumes/{logical}.tar"}
            )
        postgres_artifact = _artifact(
            postgres_path,
            kind="postgres_dump",
            name=DATABASE_NAME,
        ) | {"path": "postgres/crawlseo.sql"}
        manifest = _manifest(snapshot_id, compose_file, volume_names, volume_artifacts, postgres_artifact)
        _atomic_json(temporary / "manifest.json", manifest)
        os.chmod(temporary, 0o700)
        os.replace(temporary, final)
        return final
    except BaseException as exc:
        operation_error = exc
        if temporary.exists() and not temporary.is_symlink():
            _remove_tree(temporary)
        raise
    finally:
        if database_started and not database_stopped:
            try:
                _stop_database(compose_file, project_name=project_name)
            except BackupError as cleanup_error:
                if operation_error is None:
                    raise
                operation_error.add_note(f"database cleanup failed: {cleanup_error}")
        try:
            _restore_service_state(compose_file, running, project_name=project_name)
        except BackupError as cleanup_error:
            # Preserve the original failure if any.  A failed state restoration
            # remains visible through the command's non-zero result.
            if operation_error is None:
                raise
            operation_error.add_note(f"service state restoration failed: {cleanup_error}")


def _manifest_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise BackupError("snapshot manifest has no artifacts")
    normalized: list[dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            raise BackupError("snapshot manifest artifact is invalid")
        path = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        if (
            not isinstance(path, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.lower())
            or not isinstance(size, int)
            or size < 0
        ):
            raise BackupError("snapshot manifest artifact is invalid")
        item = dict(item)
        item["path"] = _safe_relative(path)
        item["sha256"] = digest.lower()
        if not item["path"]:
            raise BackupError("snapshot manifest artifact path is empty")
        normalized.append(item)
    paths = [str(item["path"]) for item in normalized]
    if len(paths) != len(set(paths)):
        raise BackupError("snapshot manifest has duplicate artifacts")
    return normalized


def _validate_manifest(manifest: object) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise BackupError("unsupported snapshot manifest")
    if not isinstance(manifest.get("snapshot_id"), str) or not manifest["snapshot_id"]:
        raise BackupError("snapshot manifest ID is invalid")
    if not isinstance(manifest.get("created_at"), str) or not manifest["created_at"]:
        raise BackupError("snapshot manifest timestamp is invalid")
    scope = manifest.get("scope")
    if not isinstance(scope, dict) or scope.get("included_non_sensitive_product_volumes") != list(PRODUCT_VOLUMES):
        raise BackupError("snapshot scope is invalid")
    exclusions = scope.get("excluded_non_sensitive_categories")
    if not isinstance(exclusions, list):
        raise BackupError("snapshot exclusions are missing")
    if manifest.get("excluded_non_sensitive_categories") != exclusions:
        raise BackupError("snapshot exclusion indexes are inconsistent")
    actual_exclusions = {
        item.get("category")
        for item in exclusions
        if isinstance(item, dict) and isinstance(item.get("category"), str)
    }
    required_exclusions = {item["category"] for item in EXCLUDED_CATEGORIES}
    if not required_exclusions.issubset(actual_exclusions):
        raise BackupError("snapshot exclusion boundary is incomplete")
    postgres_scope = scope.get("postgres")
    if (
        not isinstance(postgres_scope, dict)
        or postgres_scope.get("service") != DATABASE_SERVICE
        or postgres_scope.get("database") != DATABASE_NAME
        or postgres_scope.get("mode") != "logical_dump"
    ):
        raise BackupError("snapshot database scope is invalid")
    artifacts = _manifest_artifacts(manifest)
    volume_items = [item for item in artifacts if item.get("kind") == "volume"]
    postgres_items = [item for item in artifacts if item.get("kind") == "postgres_dump"]
    if {item.get("name") for item in volume_items} != set(PRODUCT_VOLUMES) or len(volume_items) != len(PRODUCT_VOLUMES):
        raise BackupError("snapshot volume scope is invalid")
    if len(postgres_items) != 1 or postgres_items[0].get("name") != DATABASE_NAME:
        raise BackupError("snapshot database artifact is invalid")
    if manifest.get("volumes") != volume_items or manifest.get("postgres") != postgres_items:
        raise BackupError("snapshot artifact indexes are inconsistent")
    expected_paths = {f"volumes/{logical}.tar" for logical in PRODUCT_VOLUMES}
    expected_paths.add("postgres/crawlseo.sql")
    if {str(item["path"]) for item in artifacts} != expected_paths:
        raise BackupError("snapshot artifact paths are invalid")
    return artifacts


def _validate_tar(path: Path) -> None:
    try:
        with tarfile.open(path, mode="r:*") as archive:
            seen: set[str] = set()
            for member in archive:
                relative = _safe_relative(member.name)
                if not relative:
                    continue
                if relative in seen:
                    raise BackupError("snapshot archive has duplicate paths")
                seen.add(relative)
                if member.issym() or member.islnk():
                    raise BackupError("snapshot archive contains a link")
                if not (member.isdir() or member.isfile()):
                    raise BackupError("snapshot archive contains an unsupported entry")
    except (OSError, tarfile.TarError) as exc:
        raise BackupError(f"invalid volume archive {path.name}") from exc


def verify_snapshot(snapshot: Path, backup_dir: Path | None = None) -> dict[str, Any]:
    """Verify manifest structure, hashes, archive paths, and exclusion scope."""

    snapshot = Path(snapshot).expanduser()
    root = Path(backup_dir).expanduser() if backup_dir is not None else snapshot.parent
    root = _secure_backup_root(root, create=False)
    if not snapshot.is_absolute():
        snapshot = root / snapshot
    _reject_symlink_components(root, snapshot)
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise BackupError("snapshot directory does not exist")
    if os.name != "nt" and stat.S_IMODE(snapshot.stat().st_mode) & 0o077:
        raise BackupError("snapshot directory permissions are too broad")
    manifest_path = snapshot / "manifest.json"
    _reject_symlink_components(snapshot, manifest_path)
    if not manifest_path.is_file():
        raise BackupError("snapshot manifest is missing")
    if os.name != "nt" and stat.S_IMODE(manifest_path.stat().st_mode) & 0o077:
        raise BackupError("snapshot manifest permissions are too broad")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("snapshot manifest cannot be read") from exc
    artifacts = _validate_manifest(manifest)
    expected_files = {"manifest.json"}
    for artifact in artifacts:
        relative = str(artifact["path"])
        artifact_path = _path_in(snapshot, relative)
        if not artifact_path.is_file() or artifact_path.is_symlink():
            raise BackupError(f"snapshot artifact is missing: {relative}")
        # Windows exposes ACL-backed files as 0666/0777 through ``stat`` even
        # after ``chmod(0600/0700)``.  The requested Unix mode is enforced on
        # POSIX hosts and remains best-effort on Windows.
        if os.name != "nt" and stat.S_IMODE(artifact_path.stat().st_mode) & 0o077:
            raise BackupError("snapshot artifact permissions are too broad")
        digest, size = _hash_file(artifact_path)
        if digest != artifact["sha256"] or size != artifact["size"]:
            raise BackupError(f"snapshot hash mismatch: {relative}")
        expected_files.add(relative)
        if artifact.get("kind") == "volume":
            _validate_tar(artifact_path)
    for directory, directories, files in os.walk(snapshot, topdown=True, followlinks=False):
        for name in directories + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise BackupError("snapshot contains a symlink")
        for name in files:
            relative = Path(directory, name).relative_to(snapshot).as_posix()
            if relative not in expected_files:
                raise BackupError(f"unexpected snapshot file: {relative}")
    return {
        "status": "verified",
        "snapshot": str(snapshot),
        "snapshot_id": manifest["snapshot_id"],
        "artifacts": len(artifacts),
        "manifest": manifest,
    }


def _extract_archive(path: Path, destination: Path) -> None:
    _ensure_directory(destination)
    try:
        with tarfile.open(path, mode="r:*") as archive:
            members = archive.getmembers()
            seen: set[str] = set()
            for member in members:
                relative = _safe_relative(member.name)
                if not relative:
                    continue
                if relative in seen:
                    raise BackupError("snapshot archive has duplicate paths")
                seen.add(relative)
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise BackupError("snapshot archive contains an unsafe entry")
                target = _path_in(destination, relative)
                _reject_symlink_components(destination, target.parent)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    os.chmod(target, member.mode & 0o777)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                source = archive.extractfile(member)
                if source is None:
                    raise BackupError("snapshot archive file cannot be read")
                try:
                    with temporary.open("wb") as handle:
                        shutil.copyfileobj(source, handle)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temporary, member.mode & 0o777)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
    except (OSError, tarfile.TarError) as exc:
        raise BackupError(f"unable to extract {path.name}") from exc


def _stage_snapshot(snapshot: Path, manifest: dict[str, Any], destination: Path) -> Path:
    _ensure_directory(destination)
    artifacts = _manifest_artifacts(manifest)
    for artifact in artifacts:
        source = _path_in(snapshot, str(artifact["path"]))
        if artifact.get("kind") == "volume":
            logical = artifact.get("name")
            if logical not in PRODUCT_VOLUMES:
                raise BackupError("snapshot volume scope is invalid")
            _extract_archive(source, destination / "volumes" / str(logical))
        else:
            target = destination / "postgres" / "crawlseo.sql"
            _ensure_directory(target.parent)
            shutil.copyfile(source, target)
            os.chmod(target, 0o600)
    return destination


def _restore_volume(actual: str, image: str, source: Path) -> None:
    _run(["docker", "volume", "inspect", actual])
    mount_volume = f"type=volume,source={actual},target=/restore-target"
    mount_source = f"type=bind,source={source},target=/restore-source,readonly"
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--mount",
            mount_volume,
            "--mount",
            mount_source,
            image,
            "python",
            "-c",
            RESTORE_PYTHON,
        ]
    )


def _restore_database(compose_file: Path, source: Path, *, project_name: str | None = None) -> None:
    command = _compose_args(
        compose_file,
        "exec",
        "-T",
        DATABASE_SERVICE,
        "psql",
        "--set=ON_ERROR_STOP=1",
        f"--username={DATABASE_USER}",
        f"--dbname={DATABASE_NAME}",
        project_name=project_name,
    )
    _run_stdin_file(command, source)


def _apply_restore(
    snapshot: Path,
    manifest: dict[str, Any],
    compose_file: Path,
    *,
    project_name: str | None = None,
) -> dict[str, Any]:
    volume_names, image = _compose_metadata(compose_file, project_name=project_name)
    running = _running_services(compose_file, project_name=project_name)
    stage_parent = Path(tempfile.mkdtemp(prefix="extella-restore-"))
    os.chmod(stage_parent, 0o700)
    database_started = False
    try:
        stage = _stage_snapshot(snapshot, manifest, stage_parent)
        _stop_non_database(compose_file, running, project_name=project_name)
        database_started = _start_database_if_needed(compose_file, running, project_name=project_name)
        _restore_database(compose_file, stage / "postgres" / "crawlseo.sql", project_name=project_name)
        for logical in PRODUCT_VOLUMES:
            _restore_volume(volume_names[logical], image, stage / "volumes" / logical)
        return {
            "status": "restored",
            "snapshot": str(snapshot),
            "snapshot_id": manifest["snapshot_id"],
            "volumes": list(PRODUCT_VOLUMES),
            "postgres": DATABASE_NAME,
        }
    finally:
        if database_started:
            try:
                _stop_database(compose_file, project_name=project_name)
            except BackupError:
                pass
        try:
            _restore_service_state(compose_file, running, project_name=project_name)
        finally:
            _remove_tree(stage_parent)


def restore_check(
    snapshot: Path,
    backup_dir: Path | None = None,
    *,
    apply: bool = False,
    compose_file: Path = COMPOSE_FILE,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Verify and stage a snapshot, optionally applying it with explicit consent."""

    result = verify_snapshot(Path(snapshot), backup_dir)
    if apply:
        return _apply_restore(
            Path(result["snapshot"]),
            result["manifest"],
            Path(compose_file),
            project_name=project_name,
        )
    root = Path(backup_dir).expanduser() if backup_dir is not None else Path(result["snapshot"]).parent
    temporary = Path(tempfile.mkdtemp(prefix="extella-restore-check-", dir=str(root)))
    os.chmod(temporary, 0o700)
    try:
        _stage_snapshot(Path(result["snapshot"]), result["manifest"], temporary)
        return {
            "status": "restore-check-ok",
            "snapshot": result["snapshot"],
            "snapshot_id": result["snapshot_id"],
            "temporary_only": True,
            "volumes": list(PRODUCT_VOLUMES),
            "postgres_dump": "postgres/crawlseo.sql",
        }
    finally:
        _remove_tree(temporary)


def _snapshot_records(root: Path) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
    valid: list[tuple[Path, dict[str, Any]]] = []
    invalid: list[str] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name.startswith("."):
            continue
        if path.is_symlink():
            invalid.append(path.name)
            continue
        if not path.is_dir():
            continue
        try:
            result = verify_snapshot(path, root)
            valid.append((path, result["manifest"]))
        except BackupError:
            invalid.append(path.name)
    valid.sort(key=lambda item: (str(item[1].get("created_at", "")), item[0].name), reverse=True)
    return valid, invalid


def prune_snapshots(
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    *,
    keep: int = 7,
    apply: bool = False,
) -> dict[str, Any]:
    """Plan retention cleanup.  Deletion requires explicit ``apply=True``."""

    if keep < 1:
        raise BackupError("keep must be at least 1")
    root = _secure_backup_root(Path(backup_dir), create=False)
    valid, invalid = _snapshot_records(root)
    to_delete = valid[keep:]
    deleted: list[str] = []
    if apply:
        for path, _manifest_data in to_delete:
            _reject_symlink_components(root, path)
            # A symlink race is still rejected by the recursive check before
            # the destructive operation.
            _reject_tree_symlinks(path)
            shutil.rmtree(path)
            deleted.append(path.name)
    return {
        "status": "pruned" if apply else "dry-run",
        "dry_run": not apply,
        "keep": keep,
        "kept": [path.name for path, _manifest_data in valid[:keep]],
        "planned": [path.name for path, _manifest_data in to_delete],
        "deleted": deleted,
        "invalid_retained": invalid,
    }


# Small public aliases keep the four CLI operations convenient to call from a
# local maintenance wrapper without exposing implementation-specific names.
create = create_snapshot
verify = verify_snapshot
prune = prune_snapshots


def _reject_tree_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise BackupError("refusing to remove a symlink")
    for directory, directories, files in os.walk(root, topdown=True, followlinks=False):
        for name in directories + files:
            if (Path(directory) / name).is_symlink():
                raise BackupError("refusing to remove a tree containing symlinks")


def _remove_tree(root: Path) -> None:
    if root.is_symlink():
        raise BackupError("refusing to remove a symlink")
    if not root.exists():
        return
    _reject_tree_symlinks(root)
    try:
        shutil.rmtree(root)
    except OSError as exc:
        raise BackupError("temporary backup cleanup failed") from exc


def _add_common_arguments(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    default: object = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument("--backup-dir", "--backup-root", dest="backup_dir", type=Path, default=default)
    parser.add_argument("--compose-file", type=Path, default=default)
    parser.add_argument("--project-name", default=default)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _add_common_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create an atomic product snapshot")
    _add_common_arguments(create, suppress_defaults=True)

    verify = commands.add_parser("verify", help="verify a snapshot manifest and hashes")
    verify.add_argument("snapshot", type=Path)
    _add_common_arguments(verify, suppress_defaults=True)

    restore = commands.add_parser("restore-check", help="verify and stage, or apply with --apply")
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("--apply", action="store_true", help="apply to production volumes and PostgreSQL")
    _add_common_arguments(restore, suppress_defaults=True)

    prune = commands.add_parser("prune", help="plan retention cleanup, dry-run by default")
    prune.add_argument("--keep", type=int, default=7)
    prune.add_argument("--apply", action="store_true", help="delete planned snapshots")
    prune.add_argument("--dry-run", action="store_true", help="explicitly select the default no-delete mode")
    _add_common_arguments(prune, suppress_defaults=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    backup_dir = Path(args.backup_dir or DEFAULT_BACKUP_DIR)
    compose_file = Path(args.compose_file or COMPOSE_FILE)
    project_name = args.project_name
    try:
        if args.command == "create":
            result: object = {
                "status": "created",
                "snapshot": str(create_snapshot(backup_dir, compose_file, project_name=project_name)),
            }
        elif args.command == "verify":
            result = verify_snapshot(args.snapshot, backup_dir)
            result.pop("manifest", None)
        elif args.command == "restore-check":
            result = restore_check(
                args.snapshot,
                backup_dir,
                apply=args.apply,
                compose_file=compose_file,
                project_name=project_name,
            )
        elif args.command == "prune":
            if args.apply and args.dry_run:
                parser.error("--apply and --dry-run are mutually exclusive")
            result = prune_snapshots(backup_dir, keep=args.keep, apply=args.apply)
        else:
            parser.error("unknown command")
            return 2
    except (BackupError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
