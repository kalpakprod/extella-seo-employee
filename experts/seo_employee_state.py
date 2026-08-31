# description: State reader for Extella SEO Employee.

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from experts.seo_employee_targets import TargetConfigError, validate_config
from experts.seo_employee_queue import QueueError, SeoEmployeeQueue
from experts.seo_employee_targets import target_paths


ROOT_PATH = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT_PATH / "state" / "current_state.json"
CONFIG_PATH = ROOT_PATH / "config" / "config.json"
QUEUE_PATH = ROOT_PATH / "state" / "queue.json"
STATE_SCHEMA = "extella.seo_employee_state.v2"
CONFIG_SCHEMA = "extella.seo_employee_config.v2"
DEVICE_BINDING_PATH = Path(
    os.environ.get("EXTELLA_DEVICE_BINDING_FILE", "/run/bindings/device_binding.json")
)
AGENT_BINDING_PATH = Path(
    os.environ.get("EXTELLA_AGENT_BINDING_FILE", "/run/bindings/agent_binding.json")
)
_SECRET_KEY = re.compile(r"(?i)(secret|token|cookie|authorization|api[_-]?key|password|oauth)")
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+\S+|sk-[a-z0-9_-]{8,}|api[_-]?key\s*[:=])")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_AGENT_ID_RE = re.compile(r"^agent_[A-Za-z0-9_][A-Za-z0-9_-]{2,127}$")


def _timestamp(now_provider: Callable[[], datetime] | None) -> str:
    value = (now_provider or (lambda: datetime.now(timezone.utc)))()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_config(path: Path, target_id: str | None = None) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or _contains_secret(value):
        return None
    try:
        config = validate_config(value)
    except TargetConfigError:
        return None
    targets = config["targets"]
    selected = [item for item in targets if item["target_id"] == target_id] if target_id else targets if len(targets) == 1 else []
    return dict(selected[0]) if len(selected) == 1 else None


def _contains_secret(value: object) -> bool:
    if isinstance(value, dict):
        return any(_SECRET_KEY.search(str(key)) or _contains_secret(nested) for key, nested in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and _SECRET_VALUE.search(value) is not None


def _next_scheduled_run(config: dict[str, object] | None, checked_at: str) -> str | None:
    if not isinstance(config, dict):
        return None
    scheduled = config.get("daily_run_time")
    timezone_name = config.get("timezone")
    if not isinstance(scheduled, str) or not isinstance(timezone_name, str) or not _TIME_RE.fullmatch(scheduled):
        return None
    try:
        checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        if checked.tzinfo is None:
            return None
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    hour, minute = (int(part) for part in scheduled.split(":"))
    local = checked.astimezone(zone)
    candidate = datetime(local.year, local.month, local.day, hour, minute, tzinfo=zone)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _empty_binding() -> dict[str, object]:
    return {
        "hosting_profile": None,
        "host": None,
        "platform_profile_id": None,
        "account_ref": None,
        "agent_ids": None,
        "since": None,
        "device_id": None,
    }


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_binding(
    device_binding_path: Path = DEVICE_BINDING_PATH,
    agent_binding_path: Path = AGENT_BINDING_PATH,
) -> dict[str, object]:
    device_exists = device_binding_path.is_file()
    agent_exists = agent_binding_path.is_file()
    if not device_exists and not agent_exists:
        return _empty_binding()
    device = _read_json(device_binding_path) if device_exists else None
    agent = _read_json(agent_binding_path) if agent_exists else None
    if device_exists and (
        device is None
        or set(device) != {"device_id", "host", "hosting_profile", "since"}
        or not isinstance(device.get("device_id"), str)
        or not _DEVICE_ID_RE.fullmatch(str(device.get("device_id")))
        or not isinstance(device.get("host"), str)
        or not device["host"].strip()
        or device["host"] != device["host"].strip()
        or device.get("hosting_profile") not in {"local", "server", "client_server"}
        or not _is_timestamp(device.get("since"))
    ):
        return _empty_binding()
    if agent_exists and (
        agent is None
        or set(agent) != {"agent_id"}
        or not isinstance(agent.get("agent_id"), str)
        or not _AGENT_ID_RE.fullmatch(str(agent.get("agent_id")))
    ):
        return _empty_binding()
    binding = _empty_binding()
    if device is not None:
        binding.update(
            {
                "hosting_profile": device["hosting_profile"],
                "host": device["host"],
                "since": device["since"],
                "device_id": device["device_id"],
                "agent_ids": [],
            }
        )
    if agent is not None:
        binding["agent_ids"] = [agent["agent_id"]]
    return binding


def _state_document(
    *,
    checked_at: str,
    config: dict[str, object] | None,
    state: str = "empty",
    error: dict[str, str] | None = None,
    device_binding_path: Path = DEVICE_BINDING_PATH,
    agent_binding_path: Path = AGENT_BINDING_PATH,
    queue_path: Path = QUEUE_PATH,
    target_id: str | None = None,
) -> dict[str, object]:
    return {
        "status": "success",
        "schema": STATE_SCHEMA,
        "state": state,
        "run_id": None,
        "updated_at": checked_at,
        "config": config,
        "last_report": None,
        "enabled": config is not None,
        "active_version": "2.0.0",
        "last_run": None,
        "last_result": "failed" if error else None,
        "last_error": error,
        "schedules": (
            [{"id": "seo_employee_daily_scan", "active": True, "next_run": _next_scheduled_run(config, checked_at)}]
            if config
            else []
        ),
        "checked_at": checked_at,
        "bound_to": _read_binding(device_binding_path, agent_binding_path),
        "queue": _queue_state(queue_path, target_id),
    }


def _upgrade_state(
    value: dict[str, object],
    *,
    checked_at: str,
    config: dict[str, object] | None,
    device_binding_path: Path = DEVICE_BINDING_PATH,
    agent_binding_path: Path = AGENT_BINDING_PATH,
    queue_path: Path = QUEUE_PATH,
    target_id: str | None = None,
) -> dict[str, object]:
    """Keeps the product schema while adding the standard Console state envelope."""
    result = dict(value)
    state = str(result.get("state", "empty"))
    run_id = result.get("run_id")
    updated_at = result.get("updated_at") if isinstance(result.get("updated_at"), str) else checked_at
    result.setdefault("status", "success")
    # The public reader always exposes the selected target only.  A persisted
    # pre-v2 envelope may contain the full multi-target config, but it must not
    # leak into one target's state or schedule calculation.
    result["config"] = config
    result.setdefault("last_report", None)
    result["enabled"] = config is not None
    result["active_version"] = "2.0.0"
    result.setdefault(
        "last_run",
        {"at": updated_at, "kind": "unknown"} if isinstance(run_id, str) and run_id else None,
    )
    result.setdefault("last_result", {"ready": "ok", "partial": "partial", "failed": "failed"}.get(state))
    result.setdefault("last_error", None)
    result.setdefault(
        "schedules",
        ([{"id": "seo_employee_daily_scan", "active": True, "next_run": _next_scheduled_run(config, checked_at)}] if config else []),
    )
    schedules = result.get("schedules")
    if isinstance(schedules, list):
        for schedule in schedules:
            if isinstance(schedule, dict) and schedule.get("id") == "seo_employee_daily_scan":
                schedule["next_run"] = _next_scheduled_run(config, checked_at)
    result.setdefault("checked_at", checked_at)
    result["bound_to"] = _read_binding(device_binding_path, agent_binding_path)
    result["queue"] = _queue_state(queue_path, target_id)
    return result


def _queue_state(queue_path: Path, target_id: str | None) -> dict[str, object]:
    """Expose only QueueItem's public persisted representation and FIFO position."""
    try:
        items = SeoEmployeeQueue(queue_path).snapshot()
    except QueueError:
        return {"items": [], "position": None}
    public = [item.to_dict() for item in items]
    active = [item for item in items if item.status in {"queued", "running"}]
    position = next(
        (index + 1 for index, item in enumerate(active) if item.target_id == target_id),
        None,
    )
    return {"items": public, "position": position}


def _configuration_status(path: Path) -> tuple[str, list[dict[str, object]]]:
    """Differentiate no configuration from unreadable or unsafe configuration."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "empty", []
    except (OSError, json.JSONDecodeError):
        return "invalid", []
    if not isinstance(value, dict) or _contains_secret(value):
        return "invalid", []
    try:
        config = validate_config(value)
    except TargetConfigError:
        return "invalid", []
    return "ok", [dict(item) for item in config["targets"]]


def list_target_states(
    *,
    config_path: Path = CONFIG_PATH,
    now_provider: Callable[[], datetime] | None = None,
    device_binding_path: Path = DEVICE_BINDING_PATH,
    agent_binding_path: Path = AGENT_BINDING_PATH,
) -> dict[str, object]:
    """Return a bounded, secret-free state summary for every configured target."""
    configuration_status, configured = _configuration_status(config_path)
    if configuration_status == "invalid":
        return {
            "status": "error",
            "error": {
                "code": "SEO_CONFIGURATION_INVALID",
                "message_ru": "Конфигурация недоступна или имеет неверный формат.",
                "message_en": "Configuration is unavailable or invalid.",
            },
        }
    root = config_path.parent.parent
    targets: list[dict[str, object]] = []
    for target in configured:
        target_id = str(target["target_id"])
        state_path = target_paths(root, target_id)["state"]
        value = json.loads(seo_employee_state(
            target_id=target_id, state_path=state_path, config_path=config_path,
            now_provider=now_provider, device_binding_path=device_binding_path,
            agent_binding_path=agent_binding_path,
        ))
        queue = value.get("queue") if isinstance(value.get("queue"), dict) else {}
        targets.append({
            "target_id": target_id,
            "target_name": target["target_name"],
            "profile": target["profile"],
            "site_url": target["site_url"],
            "state": value.get("state", "failed"),
            "queue_position": queue.get("position"),
        })
    return {"status": "success", "targets": targets}


def seo_employee_state(
    method: str = "state",
    *,
    target_id: str | None = None,
    state_path: Path = STATE_PATH,
    config_path: Path = CONFIG_PATH,
    now_provider: Callable[[], datetime] | None = None,
    device_binding_path: Path = DEVICE_BINDING_PATH,
    agent_binding_path: Path = AGENT_BINDING_PATH,
) -> str:
    if method != "state":
        return json.dumps(
            {
                "status": "error",
                "error": {
                    "code": "SEO_STATE_METHOD_UNSUPPORTED",
                    "message_ru": "Поддерживается только чтение состояния.",
                    "message_en": "Only state reading is supported.",
                },
            },
            ensure_ascii=False,
        )
    configuration_status, targets = _configuration_status(config_path)
    queue_path = config_path.parent.parent / "state" / "queue.json"
    checked_at = _timestamp(now_provider)
    if configuration_status == "invalid":
        return json.dumps(_state_document(
            checked_at=checked_at, config=None, state="failed",
            error={
                "code": "SEO_CONFIGURATION_INVALID",
                "message_ru": "Конфигурация недоступна или имеет неверный формат.",
                "message_en": "Configuration is unavailable or invalid.",
            },
            device_binding_path=device_binding_path, agent_binding_path=agent_binding_path,
            queue_path=queue_path, target_id=target_id,
        ), ensure_ascii=False)
    selected = [item for item in targets if item["target_id"] == target_id] if target_id else targets if len(targets) == 1 else []
    if target_id and not selected:
        return json.dumps({
            "status": "error",
            "error": {
                "code": "SEO_TARGET_NOT_FOUND",
                "message_ru": "Выбранная цель не настроена.",
                "message_en": "The selected target is not configured.",
            },
        }, ensure_ascii=False)
    config = dict(selected[0]) if len(selected) == 1 else None
    selected_target_id = str(config["target_id"]) if isinstance(config, dict) else None
    if state_path == STATE_PATH and isinstance(config, dict):
        state_path = target_paths(config_path.parent.parent, selected_target_id)["state"]
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        value = _state_document(
            checked_at=checked_at,
            config=config,
            device_binding_path=device_binding_path,
            agent_binding_path=agent_binding_path,
            queue_path=queue_path,
            target_id=selected_target_id,
        )
    except (OSError, json.JSONDecodeError):
        value = _state_document(
            checked_at=checked_at,
            config=config,
            state="failed",
            error={
                "code": "SEO_STATE_UNAVAILABLE",
                "message_ru": "Состояние недоступно. Проверьте локальное хранилище.",
                "message_en": "State is unavailable. Check local storage.",
            },
            device_binding_path=device_binding_path,
            agent_binding_path=agent_binding_path,
            queue_path=queue_path,
            target_id=selected_target_id,
        )
    if (
        not isinstance(value, dict)
        or value.get("schema") != STATE_SCHEMA
        or value.get("state") not in {"empty", "running", "ready", "partial", "failed"}
        or _contains_secret(value)
    ):
        value = _state_document(
            checked_at=checked_at,
            config=config,
            state="failed",
            error={
                "code": "SEO_STATE_INVALID",
                "message_ru": "Состояние имеет неверную схему.",
                "message_en": "State has an invalid schema.",
            },
            device_binding_path=device_binding_path,
            agent_binding_path=agent_binding_path,
            queue_path=queue_path,
            target_id=selected_target_id,
        )
    else:
        value = _upgrade_state(
            value,
            checked_at=checked_at,
            config=config,
            device_binding_path=device_binding_path,
            agent_binding_path=agent_binding_path,
            queue_path=queue_path,
            target_id=selected_target_id,
        )
    if target_id is None and len(targets) > 1:
        value["targets"] = list_target_states(
            config_path=config_path, now_provider=now_provider,
            device_binding_path=device_binding_path, agent_binding_path=agent_binding_path,
        )["targets"]
    return json.dumps(value, ensure_ascii=False)
