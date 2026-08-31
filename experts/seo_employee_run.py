# description: Synchronous public dispatcher for Extella SEO Employee.

from __future__ import annotations

import ipaddress
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, TypedDict

try:
    from seo_employee_queue import QUEUE_MODES, QueueError, SeoEmployeeQueue
except ModuleNotFoundError:  # Direct package import in local contract tests.
    from experts.seo_employee_queue import QUEUE_MODES, QueueError, SeoEmployeeQueue


AGENT_ZERO_BASE_URL = os.environ.get("EXTELLA_AGENT_ZERO_BASE_URL", "http://127.0.0.1:50081")
AGENT_ZERO_MESSAGE_PATH = "/api/api_message"
AGENT_ZERO_API_KEY_FILE = Path(
    os.environ.get(
        "EXTELLA_AGENT_ZERO_API_KEY_FILE",
        str(Path.home() / ".extella-seo-employee" / "agent_zero_api_key"),
    )
)
AGENT_ZERO_TIMEOUT_SECONDS = 60.0
NO_TOOLS_PROFILE_ID = "seo_employee_no_tools"
AGENT_ZERO_NO_TOOLS_PROFILE = os.environ.get("EXTELLA_AGENT_ZERO_NO_TOOLS_PROFILE", "").strip()
AGENT_ZERO_NO_TOOLS_ASSERTION_FILE = Path(
    os.environ.get(
        "EXTELLA_AGENT_ZERO_NO_TOOLS_ASSERTION_FILE",
        "/run/bindings/agent_zero_no_tools_profile.json",
    )
)
NO_TOOLS_PROFILE_SCHEMA = "extella.agent_zero_no_tools_profile.v1"


class AgentZeroReply(TypedDict):
    context_id: str
    response: str


class AgentZeroTransportError(RuntimeError):
    pass


def _load_no_tools_profile(
    profile: str = AGENT_ZERO_NO_TOOLS_PROFILE,
    assertion_file: Path = AGENT_ZERO_NO_TOOLS_ASSERTION_FILE,
) -> str:
    if profile != NO_TOOLS_PROFILE_ID:
        raise AgentZeroTransportError("Agent Zero no-tools profile is not configured")
    try:
        value = json.loads(assertion_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentZeroTransportError("Agent Zero no-tools profile assertion is unavailable") from error
    expected = {
        "schema": NO_TOOLS_PROFILE_SCHEMA,
        "agent_profile": profile,
        "tool_policy": {
            "mode": "custom",
            "default": "block",
            "mcp_default": "block",
            "allowed": [],
            "blocked": [],
        },
        "skill_policy": {
            "mode": "custom",
            "default": "block",
            "allowed": [],
            "blocked": [],
        },
    }
    if value != expected:
        raise AgentZeroTransportError("Agent Zero no-tools profile assertion is invalid")
    return profile


def _validate_local_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    host = parsed.hostname or ""
    try:
        is_loopback = host.lower() in {"localhost", "agent-zero", "agent-zero-proxy", "host.docker.internal"} \
            or ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False

    if (
        parsed.scheme != "http"
        or not is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise AgentZeroTransportError("Agent Zero URL must be a plain loopback HTTP origin")
    try:
        if parsed.port is None:
            raise AgentZeroTransportError("Agent Zero URL must include an explicit port")
    except ValueError as error:
        raise AgentZeroTransportError("Agent Zero URL contains an invalid port") from error
    return base_url.rstrip("/")


def _read_agent_zero_api_key(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if path.suffix.lower() == ".json":
            value = json.loads(raw)
            raw = value.get("mcp_server_token", "") if isinstance(value, dict) else ""
    except (OSError, json.JSONDecodeError) as error:
        raise AgentZeroTransportError("Agent Zero API key file is unavailable") from error
    if not isinstance(raw, str) or not raw.strip():
        raise AgentZeroTransportError("Agent Zero API key file is empty")
    return raw.strip()


def _call_agent_zero(
    message: str,
    *,
    base_url: str = AGENT_ZERO_BASE_URL,
    api_key_file: Path = AGENT_ZERO_API_KEY_FILE,
    timeout_seconds: float = AGENT_ZERO_TIMEOUT_SECONDS,
    no_tools_profile: str = AGENT_ZERO_NO_TOOLS_PROFILE,
    no_tools_assertion_file: Path = AGENT_ZERO_NO_TOOLS_ASSERTION_FILE,
) -> AgentZeroReply:
    message = str(message).strip()
    if not message:
        raise AgentZeroTransportError("Message is required")
    if timeout_seconds <= 0:
        raise AgentZeroTransportError("Timeout must be positive")

    profile = _load_no_tools_profile(no_tools_profile, no_tools_assertion_file)
    api_key = _read_agent_zero_api_key(api_key_file)

    request = urllib.request.Request(
        _validate_local_base_url(base_url) + AGENT_ZERO_MESSAGE_PATH,
        data=json.dumps(
            {
                "message": message,
                "lifetime_hours": 1,
                "agent_profile": profile,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-API-KEY": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise AgentZeroTransportError(f"Agent Zero returned HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise AgentZeroTransportError("Agent Zero is unavailable") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AgentZeroTransportError("Agent Zero returned invalid JSON") from None

    context_id = payload.get("context_id") if isinstance(payload, dict) else None
    answer = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(context_id, str) or not context_id or not isinstance(answer, str) or not answer:
        raise AgentZeroTransportError("Agent Zero response does not match the documented API shape")
    return {"context_id": context_id, "response": answer}


_PREFLIGHT_PROMPT = (
    "Do not use tools. Return only this JSON object with exactly two string fields: "
    '{"business_impact":"Проверка маршрута завершена.",'
    '"minimal_fix":"Дополнительных действий не требуется."}'
)

ROOT_PATH = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_PATH / "config" / "config.json"
QUEUE_PATH = ROOT_PATH / "state" / "queue.json"


def _error(code: str, message_ru: str, message_en: str) -> str:
    return json.dumps(
        {
            "status": "error",
            "error": {"code": code, "message_ru": message_ru, "message_en": message_en},
        },
        ensure_ascii=False,
    )


def _safe_error(code: str) -> str:
    messages = {
        "ownership_confirmation_required": (
            "Подтвердите право управлять сайтом перед запуском.",
            "Confirm authority to manage the site before running.",
        ),
        "SEO_CONFIGURATION_INVALID": (
            "Проверьте параметры настроенной цели.",
            "Check the configured target parameters.",
        ),
        "SEO_RUN_INPUT_INVALID": (
            "Проверьте идентификатор цели и режим запуска.",
            "Check the target ID and run mode.",
        ),
        "SEO_QUEUE_UNAVAILABLE": (
            "Очередь запусков недоступна. Проверьте локальное хранилище.",
            "The run queue is unavailable. Check local storage.",
        ),
        "SEO_CONFIGURATION_UNAVAILABLE": (
            "Конфигурация недоступна. Проверьте локальное хранилище.",
            "Configuration is unavailable. Check local storage.",
        ),
    }
    ru, en = messages.get(code, messages["SEO_RUN_INPUT_INVALID"])
    return _error(code, ru, en)


def _target(config: Mapping[str, object], target_id: str) -> dict[str, object]:
    targets = config.get("targets")
    if not isinstance(targets, list):
        raise ValueError("target is invalid")
    selected = [item for item in targets if isinstance(item, dict) and item.get("target_id") == target_id]
    if len(selected) != 1:
        raise ValueError("target is invalid")
    return dict(selected[0])


def _queue_payload(item: object, *, duplicate: bool) -> dict[str, object]:
    if not hasattr(item, "to_dict"):
        raise ValueError("queue item is invalid")
    public = item.to_dict()
    return {
        "status": "success",
        "method": "run",
        "state": "queued",
        "duplicate": duplicate,
        "queue_item": public,
    }


def process_queue_once(
    *,
    queue: SeoEmployeeQueue,
    worker: Callable[..., Mapping[str, object]] | None = None,
    config_path: Path = CONFIG_PATH,
) -> dict[str, object]:
    """Claim and settle one persisted item.  This is an internal/admin path."""
    item = queue.claim_next()
    if item is None:
        return {"status": "success", "state": "idle", "queue_item": None}
    if worker is None:
        try:
            from seo_employee_service import run_seo_employee
        except ModuleNotFoundError:
            from experts.seo_employee_service import run_seo_employee

        worker = run_seo_employee
    try:
        result = worker(
            site_url="",
            target_id=item.target_id,
            trigger=item.trigger,
            mode=item.mode,
            config_path=config_path,
        )
        if not isinstance(result, Mapping) or result.get("state") == "failed":
            queue.fail(item.queue_id, "run_failed")
            return {"status": "failed", "state": "failed", "queue_item": item.to_dict()}
        queue.complete(item.queue_id)
        return {"status": "success", "state": str(result.get("state", "ready")), "queue_item": item.to_dict()}
    except Exception:
        try:
            queue.fail(item.queue_id, "run_failed")
        except QueueError:
            pass
        return {"status": "failed", "state": "failed", "queue_item": item.to_dict()}


class QueueConsumer:
    """Single-worker queue consumer with explicit startup recovery and bounded waits."""

    def __init__(
        self,
        *,
        queue: SeoEmployeeQueue,
        worker: Callable[..., Mapping[str, object]] | None = None,
        config_path: Path = CONFIG_PATH,
        wake_seconds: float = 30.0,
    ) -> None:
        if wake_seconds <= 0:
            raise ValueError("wake_seconds must be positive")
        self.queue = queue
        self.worker = worker
        self.config_path = config_path
        self.wake_seconds = wake_seconds
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.recovery_count = 0

    def start(self) -> None:
        if self.thread is not None:
            return
        self.queue.recover_interrupted()
        self.recovery_count = 1
        self.wake.set()
        self.thread = threading.Thread(target=self._serve, name="seo-employee-queue", daemon=True)
        self.thread.start()

    def notify(self) -> None:
        self.wake.set()

    def _serve(self) -> None:
        while not self.stop_event.is_set():
            self.wake.wait(self.wake_seconds)
            self.wake.clear()
            if self.stop_event.is_set():
                break
            process_queue_once(queue=self.queue, worker=self.worker, config_path=self.config_path)
            if any(item.status == "queued" for item in self.queue.snapshot()):
                self.wake.set()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        self.stop_event.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(join_timeout)
            if self.thread.is_alive():
                raise RuntimeError("SEO_QUEUE_SHUTDOWN_FAILED")


def seo_employee_run(
    method: str = "run",
    site_url: str = "",
    daily_run_time: str = "",
    timezone: str = "",
    trigger: str = "manual",
    *,
    target_id: str = "",
    target_name: str = "",
    profile: str = "service_b2b",
    language: str = "ru",
    region: str = "GLOBAL",
    site_type: str = "website",
    business_goal: str = "organic_visibility",
    max_pages: int = 25,
    mode: str = "",
    ownership_confirmed: bool = False,
    config_path: Path = CONFIG_PATH,
    queue_path: Path = QUEUE_PATH,
    queue_consumer: QueueConsumer | None = None,
) -> str:
    """Extella entrypoint. It never accepts or forwards a free-form model prompt."""
    if method not in {"configure", "run", "state", "preflight", "process_queue_once"}:
        return _error(
            "SEO_METHOD_UNSUPPORTED",
            "Поддерживаются только configure, run, state и preflight.",
            "Only configure, run, state, and preflight are supported.",
        )

    try:
        from seo_employee_service import (
            SeoEmployeeError,
            _parse_enrichment_response,
            load_configuration,
            save_configuration,
        )
    except ModuleNotFoundError:
        from experts.seo_employee_service import (
            SeoEmployeeError,
            _parse_enrichment_response,
            load_configuration,
            save_configuration,
        )

    if method == "preflight":
        try:
            reply = _call_agent_zero(_PREFLIGHT_PROMPT)
            _parse_enrichment_response(reply.get("response", ""))
        except (AgentZeroTransportError, SeoEmployeeError):
            return _error(
                "SEO_PREFLIGHT_FAILED",
                "Маршрут модели не прошёл безопасную проверку.",
                "The model route did not pass the safe preflight.",
            )
        return json.dumps({"status": "success", "method": "preflight", "result": "ok"}, ensure_ascii=False)

    if method == "configure":
        try:
            if not isinstance(ownership_confirmed, bool):
                raise ValueError("ownership confirmation is invalid")
            if config_path.exists():
                # A pre-existing unreadable file is never an empty config.
                config_path.read_text(encoding="utf-8")
            if target_id:
                existing = _target(load_configuration(config_path), target_id)
                try:
                    from seo_employee_targets import CONFIG_SCHEMA, validate_config
                except ModuleNotFoundError:
                    from experts.seo_employee_targets import CONFIG_SCHEMA, validate_config
                candidate = dict(existing)
                candidate["site_url"] = site_url
                validated = validate_config({"schema": CONFIG_SCHEMA, "targets": [candidate]})["targets"][0]
                if validated["site_url"] != existing["site_url"]:
                    raise ValueError("saved target URL is immutable")
                site_url = str(validated["site_url"])
            config = save_configuration(
                site_url, daily_run_time, timezone,
                target_name=target_name or None, profile=profile, language=language, region=region, site_type=site_type,
                business_goal=business_goal, max_pages=max_pages, mode=mode or "daily_monitor",
                ownership_confirmed=ownership_confirmed, config_path=config_path,
            )
        except OSError:
            return _safe_error("SEO_CONFIGURATION_UNAVAILABLE")
        except (SeoEmployeeError, TypeError, ValueError):
            return _safe_error("SEO_CONFIGURATION_INVALID")
        selected = _target(config, target_id) if target_id else dict(config["targets"][-1])
        return json.dumps({"status": "success", "method": "configure", "config": selected, "target_id": selected["target_id"]}, ensure_ascii=False)

    if method == "state":
        try:
            from seo_employee_state import seo_employee_state
        except ModuleNotFoundError:
            from experts.seo_employee_state import seo_employee_state

        return seo_employee_state(target_id=target_id or None, config_path=config_path)

    if method == "process_queue_once":
        try:
            return json.dumps(process_queue_once(queue=SeoEmployeeQueue(queue_path), config_path=config_path), ensure_ascii=False)
        except (QueueError, OSError, ValueError):
            return _safe_error("SEO_QUEUE_UNAVAILABLE")

    if method == "run":
        if trigger not in {"manual", "daily"} or not target_id or (mode and mode not in QUEUE_MODES):
            return _safe_error("SEO_RUN_INPUT_INVALID")
        try:
            config = load_configuration(config_path)
        except SeoEmployeeError:
            return _safe_error("SEO_QUEUE_UNAVAILABLE")
        try:
            selected = _target(config, target_id)
        except ValueError:
            return _safe_error("SEO_RUN_INPUT_INVALID")
        if selected.get("ownership_confirmed") is not True:
            return _safe_error("ownership_confirmation_required")
        try:
            try:
                from seo_employee_service import _utc_now, new_run_id
            except ModuleNotFoundError:
                from experts.seo_employee_service import _utc_now, new_run_id
            queue = SeoEmployeeQueue(queue_path)
            candidate_run_id = new_run_id()
            item = queue.enqueue(candidate_run_id, target_id, trigger, mode or str(selected["mode"]), _utc_now())
            duplicate = item.run_id != candidate_run_id
            if queue_consumer is not None:
                queue_consumer.notify()
            return json.dumps(_queue_payload(item, duplicate=duplicate), ensure_ascii=False)
        except (QueueError, OSError, TypeError, ValueError):
            return _safe_error("SEO_QUEUE_UNAVAILABLE")

    raise AssertionError("unreachable")
