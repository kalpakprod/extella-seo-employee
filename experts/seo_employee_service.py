# description: Deterministic synchronous service for Extella SEO Employee.

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from experts.seo_employee_profiles import AuditMode, AuditPlan, IndustryProfile, build_audit_plan
from experts.seo_employee_rules import canonical_rule, evidence_level, load_rule_catalog
from experts.seo_employee_targets import (
    CONFIG_SCHEMA as TARGET_CONFIG_SCHEMA,
    TargetConfigError,
    migrate_config,
    migrate_config_file,
    target_paths,
    validate_config,
)

# The source module is also used as a top-level runtime module by existing wrappers.
# Register only the domain aliases it imports before loading it through the package.
from experts import seo_employee_profiles as _seo_employee_profiles
from experts import seo_employee_rules as _seo_employee_rules
import sys

sys.modules.setdefault("seo_employee_profiles", _seo_employee_profiles)
sys.modules.setdefault("seo_employee_rules", _seo_employee_rules)
from experts.seo_employee_sources import Coverage, CrawlSEOAdapter, SEOmatorAdapter, SourceResult, required_sources_satisfied


ROOT_PATH = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_PATH / "config" / "config.json"
STATE_PATH = ROOT_PATH / "state" / "current_state.json"
REPORT_PATH = ROOT_PATH / "reports" / "latest.json"
HISTORY_DIR = ROOT_PATH / "history"
EVIDENCE_DIR = ROOT_PATH / "evidence"
BASELINE_PATH = ROOT_PATH / "state" / "baseline.json"
DAILY_INDEX_PATH = ROOT_PATH / "state" / "daily_runs.json"
LOCK_DIR = ROOT_PATH / "state" / "locks"
DEVICE_BINDING_PATH = Path(
    os.environ.get("EXTELLA_DEVICE_BINDING_FILE", "/run/bindings/device_binding.json")
)
AGENT_BINDING_PATH = Path(
    os.environ.get("EXTELLA_AGENT_BINDING_FILE", "/run/bindings/agent_binding.json")
)
OVERALL_RUN_TIMEOUT_SECONDS = 180.0
SOURCE_TIMEOUT_SECONDS = 120.0
AGENT_ZERO_TIMEOUT_SECONDS = 60.0
SERVICE_INSTANCE_ID = uuid.uuid4().hex
CRAWLSEO_EXECUTABLE = Path(
    os.environ.get("EXTELLA_CRAWLSEO_EXECUTABLE", str(ROOT_PATH / "runtime" / "container" / "run_crawlseo"))
)
SEOMATOR_EXECUTABLE = Path(
    os.environ.get("EXTELLA_SEOMATOR_EXECUTABLE", str(ROOT_PATH / "runtime" / "container" / "run_seomator"))
)
DNS_RESOLVER_URL = os.environ.get("EXTELLA_DNS_RESOLVER_URL", "")

REPORT_SCHEMA = "extella.seo_employee_report.v2"
STATE_SCHEMA = "extella.seo_employee_state.v2"
CONFIG_SCHEMA = TARGET_CONFIG_SCHEMA
NORMALIZER_VERSION = "2.0.0"
ACTIVE_VERSION = "2.0.0"
MODEL_FIELDS = frozenset(
    {
        "rule_key",
        "severity",
        "evidence_level",
        "sources",
        "affected_pages_count",
        "confirmed_fact",
        "url",
    }
)

_SECRET_KEY = re.compile(r"(?i)(secret|token|cookie|authorization|api[_-]?key|pass(?:word|wd)|oauth)")
_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|sk-[a-z0-9_-]{8,}|\b(?:token|pass(?:word|wd)|cookie|authorization|"
    r"api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*\S+)"
)
_UNSUPPORTED_CLAIM = re.compile(
    r"(?i)(%|\b(?:traffic|revenue|profit|client|customer|conversion|ctr|ranking|lead|"
    r"percent|percentage|quantity|metric|increase|decrease|because|caus(?:e|al|ality)|"
    r"many|more|less|significant(?:ly)?|double|multiple|majority|leads?|results?)\b|"
    r"трафик|выручк|доход|прибыл|клиент|покупател|конверси|позици|лид|процент|"
    r"количеств|метрик|увеличит|увеличен|снизит|снижен|привед[её]т|причин|потер[яи]|"
    r"много|большинств|значительн|существенн|кратн|вызыва|обуслов|вед[её]т\s+к)"
)
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_AGENT_ID_RE = re.compile(r"^agent_[A-Za-z0-9_][A-Za-z0-9_-]{2,127}$")
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_EVIDENCE_ORDER = {"verified": 0, "supported": 1, "unverified": 2}
_ALLOWED_FACT_WHITESPACE = frozenset({" ", "\t", "\n", "\r"})
_SAFE_SOURCE_REASONS = frozenset(
    {
        "audit_failed", "captcha", "http_403", "http_429", "http_503", "incomplete_coverage",
        "invalid_payload", "not_configured", "robots_denied", "timeout", "unsupported",
        "wrapper_unavailable", "waf",
    }
)
_SOURCE_STATUS_TEXT = {
    "unavailable": {
        "message_ru": "Источник временно недоступен, данные этого запуска не получены.",
        "message_en": "The source is temporarily unavailable, so this run has no data from it.",
        "instruction": "Retry the source after resolving its availability restriction.",
    },
    "failed": {
        "message_ru": "Источник вернул недействительные или неполные данные.",
        "message_en": "The source returned invalid or incomplete data.",
        "instruction": "Check the source output and run the audit again.",
    },
    "not_configured": {
        "message_ru": "Источник не настроен. Подключите его перед запросом данных поисковой эффективности.",
        "message_en": "The source is not configured. Connect it before requesting search-performance data.",
        "instruction": "Connect this optional source before requesting search-performance data.",
    },
    "unsupported": {
        "message_ru": "Источник не поддерживает запрошенный план проверки.",
        "message_en": "The source does not support the requested audit plan.",
        "instruction": "Use a supported source or adjust the audit plan.",
    },
}
# ponytail: process-local lock assumes one Compose replica; use a shared lease for multi-replica deployment.
_LOCK_ACQUISITION_MUTEX = threading.Lock()


class SeoEmployeeError(RuntimeError):
    pass


class ModelInputError(SeoEmployeeError):
    pass


class RunDeadline:
    """One bounded deadline shared by source collection and model enrichment."""

    def __init__(self, timeout_seconds: float = OVERALL_RUN_TIMEOUT_SECONDS) -> None:
        if timeout_seconds <= 0:
            raise SeoEmployeeError("run deadline must be positive")
        self._expires_at = time.monotonic() + timeout_seconds

    def remaining(self, cap_seconds: float) -> float:
        remaining = min(cap_seconds, self._expires_at - time.monotonic())
        if remaining <= 0:
            raise SeoEmployeeError("SEO run deadline exceeded")
        return remaining


def _worker_resolver(
    host: str,
    _port: object,
    *,
    type: int = 0,
    endpoint: str = DNS_RESOLVER_URL,
) -> list[tuple[object, object, object, object, tuple[str, int]]]:
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "dns-resolver"
        or parsed.port != 8083
        or parsed.path != "/resolve"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise OSError("DNS resolver endpoint is invalid")
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"hostname": host}, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(4097)
    if len(raw) > 4096:
        raise OSError("DNS resolver response is too large")
    value = json.loads(raw)
    addresses = value.get("addresses") if isinstance(value, dict) else None
    if not isinstance(addresses, list) or not addresses:
        raise OSError("DNS resolver returned no addresses")
    return [(None, None, type, None, (str(address), 0)) for address in addresses]


PUBLIC_RESOLVER = _worker_resolver if DNS_RESOLVER_URL else socket.getaddrinfo


def _now_utc(now_provider: Callable[[], datetime] | None = None) -> datetime:
    value = (now_provider or (lambda: datetime.now(timezone.utc)))()
    if value.tzinfo is None:
        raise SeoEmployeeError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return _iso(_now_utc())


def new_run_id() -> str:
    return f"seo-{uuid.uuid4().hex}"


def _parse_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeoEmployeeError("requested_at must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise SeoEmployeeError("requested_at must be an ISO 8601 string") from error
    if parsed.tzinfo is None:
        raise SeoEmployeeError("requested_at must include a timezone")
    return value.strip()


def _resolved_addresses(
    host: str,
    resolver: Callable[..., Iterable[tuple[Any, ...]]],
) -> set[str]:
    try:
        return {str(ipaddress.ip_address(host))}
    except ValueError:
        pass
    try:
        return {str(ipaddress.ip_address(item[4][0])) for item in resolver(host, None, type=socket.SOCK_STREAM)}
    except (OSError, ValueError) as error:
        raise SeoEmployeeError("site_url hostname cannot be resolved") from error


def validate_public_url(
    value: object,
    *,
    resolver: Callable[..., Iterable[tuple[Any, ...]]] = PUBLIC_RESOLVER,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeoEmployeeError("site_url is required")
    raw = value.strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise SeoEmployeeError("site_url is invalid") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SeoEmployeeError("site_url must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise SeoEmployeeError("site_url must not contain userinfo")
    if port is not None and not 1 <= port <= 65535:
        raise SeoEmployeeError("site_url port is invalid")
    addresses = _resolved_addresses(parsed.hostname, resolver)
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise SeoEmployeeError("site_url must resolve only to public addresses")
    return raw


def validate_run_command(
    command: Mapping[str, object],
    *,
    resolver: Callable[..., Iterable[tuple[Any, ...]]] = PUBLIC_RESOLVER,
) -> dict[str, str]:
    required = {"target_id", "site_url", "profile", "mode", "trigger", "requested_at"}
    if set(command) != required:
        raise SeoEmployeeError("run command must contain exactly target_id, site_url, profile, mode, trigger, requested_at")
    target_id = command["target_id"]
    profile = command["profile"]
    mode = command["mode"]
    trigger = command["trigger"]
    if not isinstance(target_id, str) or not re.fullmatch(r"target-[a-z0-9][a-z0-9._-]{0,127}", target_id):
        raise SeoEmployeeError("target_id is invalid")
    try:
        IndustryProfile(profile)
        AuditMode(mode)
    except ValueError as error:
        raise SeoEmployeeError("profile or mode is invalid") from error
    if trigger not in {"manual", "daily"}:
        raise SeoEmployeeError("trigger must be manual or daily")
    return {
        "target_id": target_id,
        "site_url": validate_public_url(command["site_url"], resolver=resolver),
        "profile": str(profile),
        "mode": str(mode),
        "trigger": str(trigger),
        "requested_at": _parse_timestamp(command["requested_at"]),
    }


def _validate_schedule(daily_run_time: object, timezone_name: object) -> tuple[str, str]:
    if not isinstance(daily_run_time, str) or not _TIME_RE.fullmatch(daily_run_time):
        raise SeoEmployeeError("daily_run_time must use HH:MM")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise SeoEmployeeError("timezone is required")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise SeoEmployeeError("timezone must be an IANA timezone") from error
    return daily_run_time, timezone_name


def site_id_from_url(site_url: str) -> str:
    parsed = urllib.parse.urlsplit(site_url)
    basis = (parsed.hostname or "site").lower()
    if parsed.port is not None:
        basis += f"-{parsed.port}"
    slug = re.sub(r"[^a-z0-9._-]+", "-", basis).strip("-.") or "site"
    if len(slug) > 56:
        slug = slug[:47].rstrip("-.") + "-" + hashlib.sha256(basis.encode()).hexdigest()[:8]
    return slug


def sanitize_url_for_model(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ModelInputError("model input URL is invalid") from error
    host = parsed.hostname or ""
    if not host or parsed.scheme not in {"http", "https"}:
        raise ModelInputError("model input URL is invalid")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))


def _source_status(
    name: str,
    status: str,
    obtained_at: str,
    *,
    reason: str | None = None,
    coverage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "name": name,
        "status": status,
        "obtained_at": obtained_at,
        "coverage": dict(coverage or {}),
    }
    if reason:
        fallback = "source_unavailable" if status == "unavailable" else "source_failed"
        value["reason"] = reason if reason in _SAFE_SOURCE_REASONS else fallback
    if status in _SOURCE_STATUS_TEXT:
        value.update(_SOURCE_STATUS_TEXT[status])
    return value


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _source_payload_is_valid(
    source: str,
    payload: Mapping[str, object],
    plan: AuditPlan | None = None,
) -> bool:
    """Validate source output through the current adapter contract."""
    selected_plan = plan or build_audit_plan("service_b2b", requested_max_pages=1)
    adapter = CrawlSEOAdapter() if source == "CrawlSEO" else SEOmatorAdapter() if source == "SEOmator" else None
    return adapter is not None and adapter.parse(payload, selected_plan).status == "ok"


def prioritize_findings(findings: Iterable[Mapping[str, object]], limit: int = 10) -> list[dict[str, object]]:
    if limit <= 0:
        raise SeoEmployeeError("task limit must be positive")
    verified = [dict(item) for item in findings if item.get("evidence_level") != "unverified"]
    return sorted(
        verified,
        key=lambda item: (
            _SEVERITY_ORDER.get(str(item.get("severity")), 99),
            _EVIDENCE_ORDER.get(str(item.get("evidence_level")), 99),
            -int(item.get("affected_pages_count", 0)),
            str(item.get("url", "")),
            str(item.get("rule_key", "")),
        ),
    )[:limit]


def _default_agent_call(message: str, *, timeout_seconds: float = AGENT_ZERO_TIMEOUT_SECONDS) -> Mapping[str, str]:
    from seo_employee_run import _call_agent_zero

    return _call_agent_zero(message, timeout_seconds=timeout_seconds)


def _parse_enrichment_response(raw_response: object) -> dict[str, str]:
    raw = str(raw_response or "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[1:-1]).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SeoEmployeeError("Agent Zero returned invalid enrichment JSON") from error
    if not isinstance(value, dict):
        raise SeoEmployeeError("Agent Zero enrichment fields are invalid")
    return _validate_enrichment(value)


def enrich_with_agent_zero(
    model_input: Mapping[str, object],
    *,
    agent_call: Callable[[str], Mapping[str, str]] = _default_agent_call,
    timeout_seconds: float = AGENT_ZERO_TIMEOUT_SECONDS,
) -> dict[str, str]:
    validate_model_input(model_input)
    prompt = (
        "Do not use tools. Return only a JSON object with exactly the string keys "
        '"business_impact" and "minimal_fix". Write one short Russian sentence per value. '
        "Use only the supplied fact. Do not invent quantities, traffic, revenue, clients or causality. "
        "Do not use digits. FACT_INPUT="
        + json.dumps(dict(model_input), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    if agent_call is _default_agent_call:
        reply = _default_agent_call(prompt, timeout_seconds=timeout_seconds)
    else:
        reply = agent_call(prompt)
    return _parse_enrichment_response(reply.get("response", ""))


def _validate_enrichment(value: Mapping[str, object]) -> dict[str, str]:
    if set(value) != {"business_impact", "minimal_fix"}:
        raise SeoEmployeeError("Agent Zero enrichment fields are invalid")
    cleaned: dict[str, str] = {}
    for field in ("business_impact", "minimal_fix"):
        text = value.get(field)
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 600
            or any(ch.isdigit() for ch in text)
            or _UNSUPPORTED_CLAIM.search(text)
        ):
            raise SeoEmployeeError("Agent Zero enrichment contains unsupported claims")
        cleaned[field] = text.strip()
    return cleaned


def _assert_no_secret_material(value: object, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _SECRET_KEY.search(str(key)):
                raise SeoEmployeeError(f"unsafe field in snapshot at {path}")
            _assert_no_secret_material(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_secret_material(nested, f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise SeoEmployeeError(f"unsafe value in snapshot at {path}")


def atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    _assert_no_secret_material(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _safe_read_json(path: Path) -> dict[str, object] | None:
    return _read_json_object(path)


def _schedule_state(config: Mapping[str, object] | None, checked_at: str) -> list[dict[str, object]]:
    if not config:
        return []
    return [{"id": "seo_employee_daily_scan", "active": True, "next_run": _next_scheduled_run(config, checked_at)}]


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


def _valid_binding_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _read_state_binding(
    *,
    device_binding_path: Path = DEVICE_BINDING_PATH,
    agent_binding_path: Path = AGENT_BINDING_PATH,
) -> dict[str, object]:
    device_exists = device_binding_path.is_file()
    agent_exists = agent_binding_path.is_file()
    if not device_exists and not agent_exists:
        return _empty_binding()
    device = _safe_read_json(device_binding_path) if device_exists else None
    agent = _safe_read_json(agent_binding_path) if agent_exists else None
    # A partial or malformed binding must never be upgraded into a claimed identity.
    if device_exists and (
        not isinstance(device, Mapping)
        or set(device) != {"device_id", "host", "hosting_profile", "since"}
        or not isinstance(device.get("device_id"), str)
        or not _DEVICE_ID_RE.fullmatch(str(device.get("device_id")))
        or not isinstance(device.get("host"), str)
        or not device["host"].strip()
        or device["host"] != device["host"].strip()
        or device.get("hosting_profile") not in {"local", "server", "client_server"}
        or not _valid_binding_timestamp(device.get("since"))
    ):
        return _empty_binding()
    if agent_exists and (
        not isinstance(agent, Mapping)
        or set(agent) != {"agent_id"}
        or not isinstance(agent.get("agent_id"), str)
        or not _AGENT_ID_RE.fullmatch(str(agent.get("agent_id")))
    ):
        return _empty_binding()
    binding = _empty_binding()
    if isinstance(device, Mapping):
        binding.update(
            {
                "hosting_profile": device["hosting_profile"],
                "host": device["host"],
                "since": device["since"],
                "device_id": device["device_id"],
                "agent_ids": [],
            }
        )
    if isinstance(agent, Mapping):
        binding["agent_ids"] = [agent["agent_id"]]
    return binding


def make_state(
    state: str,
    *,
    checked_at: str,
    config: Mapping[str, object] | None,
    run_id: str | None = None,
    trigger: str | None = None,
    last_report: Mapping[str, object] | None = None,
    last_error: Mapping[str, str] | None = None,
    device_binding_path: Path = DEVICE_BINDING_PATH,
    agent_binding_path: Path = AGENT_BINDING_PATH,
) -> dict[str, object]:
    if state not in {"empty", "running", "ready", "partial", "failed"}:
        raise SeoEmployeeError("state is invalid")
    last_result = {"ready": "ok", "partial": "partial", "failed": "failed"}.get(state)
    return {
        "status": "success",
        "schema": STATE_SCHEMA,
        "state": state,
        "run_id": run_id,
        "updated_at": checked_at,
        "config": dict(config) if config else None,
        "last_report": dict(last_report) if last_report else None,
        "enabled": config is not None,
        "active_version": ACTIVE_VERSION,
        "last_run": {"at": checked_at, "kind": trigger} if run_id else None,
        "last_result": last_result,
        "last_error": dict(last_error) if last_error else None,
        "schedules": _schedule_state(config, checked_at),
        "checked_at": checked_at,
        "bound_to": _read_state_binding(
            device_binding_path=device_binding_path,
            agent_binding_path=agent_binding_path,
        ),
    }


def _safe_failure(code: str) -> dict[str, str]:
    messages = {
        "SEO_SOURCES_UNAVAILABLE": (
            "Источники аудита недоступны. Проверьте локальные обёртки и запустите снова вручную.",
            "Audit sources are unavailable. Check the local wrappers and run again manually.",
        ),
        "SEO_REPORT_SAVE_FAILED": (
            "Отчёт не сохранён. Проверьте локальное хранилище и запустите снова вручную.",
            "The report was not saved. Check local storage and run again manually.",
        ),
        "SEO_RUN_FAILED": (
            "Запуск завершился ошибкой. Проверьте локальную установку и запустите снова вручную.",
            "The run failed. Check the local installation and run again manually.",
        ),
    }
    ru, en = messages[code]
    return {"code": code, "message_ru": ru, "message_en": en}


def compare_with_baseline(
    tasks: Sequence[Mapping[str, object]], baseline: Mapping[str, object] | None
) -> tuple[dict[str, object], dict[str, object]]:
    current: dict[str, dict[str, object]] = {}
    for task in tasks:
        card = dict(task)
        _assert_no_secret_material(card)
        current[str(card["task_id"])] = card
    # Reports are capped to ten tasks. Preserve the same bounded, deterministic
    # set in the baseline even when this helper is called directly.
    current = {task_id: current[task_id] for task_id in sorted(current)[:10]}
    previous_items = baseline.get("items", []) if isinstance(baseline, Mapping) else []
    previous: dict[str, dict[str, object]] = {}
    if isinstance(previous_items, list):
        for item in previous_items:
            if not isinstance(item, Mapping) or not isinstance(item.get("task_id"), str):
                continue
            card = dict(item)
            try:
                _assert_no_secret_material(card)
            except SeoEmployeeError:
                continue
            previous[str(card["task_id"])] = card
    new_ids = sorted(set(current) - set(previous))
    fixed_ids = sorted(set(previous) - set(current))
    unchanged_ids = sorted(set(current) & set(previous))
    comparison: dict[str, object] = {
        "baseline": "compared" if baseline is not None else "created",
        "new": len(new_ids),
        "fixed": len(fixed_ids),
        "unchanged": len(unchanged_ids),
        "new_items": [current[item] for item in new_ids],
        "fixed_items": [previous[item] for item in fixed_ids],
        "unchanged_items": [current[item] for item in unchanged_ids],
    }
    next_baseline: dict[str, object] = {
        "schema": "extella.seo_employee_baseline.v1",
        "items": [current[item] for item in sorted(current)],
    }
    return comparison, next_baseline


def _daily_key(site_id: str, now: datetime, timezone_name: str) -> str:
    try:
        local_date = now.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise SeoEmployeeError("timezone must be an IANA timezone") from error
    return f"{site_id}|{local_date}|{timezone_name}"


def _lock_path(lock_dir: Path, site_id: str) -> Path:
    return lock_dir / f"{hashlib.sha256(site_id.encode()).hexdigest()[:16]}.lock"


def _acquire_lock(
    lock_path: Path,
    run_id: str,
    site_id: str,
    *,
    instance_id: str = SERVICE_INSTANCE_ID,
) -> tuple[bool, str]:
    with _LOCK_ACQUISITION_MUTEX:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": run_id, "site_id": site_id, "instance_id": instance_id}
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            existing = _safe_read_json(lock_path)
            existing_id = existing.get("run_id") if isinstance(existing, Mapping) else None
            owner = existing.get("instance_id") if isinstance(existing, Mapping) else None
            # Compose runs a single product replica. A different process identity can
            # therefore only be residue from a killed/restarted container, not an
            # active peer. Replace it atomically so SIGKILL cannot wedge the product.
            if owner != instance_id:
                atomic_write_json(lock_path, payload)
                return True, run_id
            return False, str(existing_id) if isinstance(existing_id, str) and existing_id else "unknown"
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")).encode())
            handle.flush()
            os.fsync(handle.fileno())
        return True, run_id


def _release_lock(lock_path: Path, run_id: str) -> None:
    existing = _safe_read_json(lock_path)
    if isinstance(existing, Mapping) and existing.get("run_id") == run_id:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _completed_daily_run(daily_index: Mapping[str, object], daily_key: str) -> str | None:
    runs = daily_index.get("runs")
    if not isinstance(runs, Mapping):
        raise SeoEmployeeError("daily run index is invalid")
    prior = runs.get(daily_key)
    if isinstance(prior, str) and prior:
        return prior
    if isinstance(prior, Mapping) and prior.get("completed") is True:
        run_id = prior.get("run_id")
        return run_id if isinstance(run_id, str) and run_id else None
    return None


def _record_completed_daily_run(
    daily_index_path: Path,
    daily_key: str,
    run_id: str,
    state: str,
) -> None:
    index = _safe_read_json(daily_index_path) or {
        "schema": "extella.seo_employee_daily_runs.v1",
        "runs": {},
    }
    if index.get("schema") != "extella.seo_employee_daily_runs.v1" or not isinstance(index.get("runs"), dict):
        raise SeoEmployeeError("daily run index is invalid")
    index["runs"][daily_key] = {"run_id": run_id, "state": state, "completed": True}
    atomic_write_json(daily_index_path, index)


def completed_daily_run_id(
    target: Mapping[str, object],
    now: datetime,
    config_path: Path = CONFIG_PATH,
) -> str | None:
    target_id = str(target["target_id"])
    daily_path = target_paths(_storage_root(config_path), target_id)["daily_index"]
    index = _safe_read_json(daily_path) or {"schema": "extella.seo_employee_daily_runs.v1", "runs": {}}
    return _completed_daily_run(index, _daily_key(target_id, now, str(target["timezone"])))


def _save_completed_report(
    report: Mapping[str, object],
    *,
    report_path: Path,
    history_dir: Path,
) -> None:
    run = report.get("run")
    if not isinstance(run, Mapping) or not isinstance(run.get("run_id"), str):
        raise SeoEmployeeError("report run identity is invalid")
    history_path = history_dir / f'{run["run_id"]}.json'
    atomic_write_json(history_path, report)
    atomic_write_json(report_path, report)


# v2 target-scoped implementation.


def _target_from_config(config: Mapping[str, object], target_id: str | None) -> dict[str, object]:
    targets = config.get("targets")
    if not isinstance(targets, list):
        raise SeoEmployeeError("SEO Employee configuration is invalid")
    selected = [target for target in targets if isinstance(target, Mapping) and target.get("target_id") == target_id]
    if target_id is None and len(targets) == 1 and isinstance(targets[0], Mapping):
        selected = [targets[0]]
    if len(selected) != 1:
        raise SeoEmployeeError("target_id is invalid")
    return dict(selected[0])


def _storage_root(config_path: Path) -> Path:
    # v2 keeps the config in <root>/config/config.json.  A caller may use a
    # different filename but the containing config directory remains canonical.
    return config_path.parent.parent


def _plan_public(plan: AuditPlan) -> dict[str, object]:
    return {
        "profile": plan.profile.value,
        "mode": plan.mode.value,
        "max_pages": plan.max_pages,
        "categories": list(plan.categories),
        "required_sources": list(plan.required_sources),
        "optional_sources": list(plan.optional_sources),
        "performance_sample_pages": plan.performance_sample_pages,
        "overall_timeout_seconds": plan.overall_timeout_seconds,
        "source_timeout_seconds": plan.source_timeout_seconds,
    }


def _plan_signature(plan: AuditPlan) -> str:
    encoded = json.dumps(_plan_public(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _catalog_major() -> str:
    version = next(iter(load_rule_catalog().values())).version
    return version.split(".", 1)[0]


def save_configuration(
    site_url: str,
    daily_run_time: str,
    timezone_name: str,
    *,
    target_name: str | None = None,
    site_type: str = "website",
    business_goal: str = "organic_visibility",
    profile: str = IndustryProfile.SERVICE_B2B.value,
    language: str = "ru",
    region: str = "GLOBAL",
    mode: str = AuditMode.DAILY_MONITOR.value,
    max_pages: int = 25,
    ownership_confirmed: bool = False,
    config_path: Path = CONFIG_PATH,
    resolver: Callable[..., Iterable[tuple[Any, ...]]] = PUBLIC_RESOLVER,
) -> dict[str, object]:
    """Create or update one validated v2 target without caller-supplied IDs."""
    if target_name is not None and (not isinstance(target_name, str) or not target_name.strip()):
        raise SeoEmployeeError("target_name is invalid")
    try:
        normalized_url = sanitize_url_for_model(validate_public_url(site_url, resolver=resolver))
        validated_time, validated_zone = _validate_schedule(daily_run_time, timezone_name)
        seed = migrate_config({
            "schema": "extella.seo_employee_config.v1",
            "site_id": site_id_from_url(normalized_url),
            "site_url": normalized_url,
            "daily_run_time": validated_time,
            "timezone": validated_zone,
        })
        target = dict(seed["targets"][0])
        target.update({
            "target_name": target_name or str(target["target_name"]),
            "site_url": normalized_url,
            "profile": profile,
            "language": language,
            "region": region,
            "site_type": site_type,
            "business_goal": business_goal,
            "daily_run_time": validated_time,
            "timezone": validated_zone,
            "max_pages": max_pages,
            "ownership_confirmed": ownership_confirmed,
            "mode": mode,
        })
        existing = _read_json_object(config_path)
        if isinstance(existing, Mapping):
            existing_v2 = migrate_config(existing)
            targets = [dict(item) for item in existing_v2["targets"]]
            for index, prior in enumerate(targets):
                if prior["site_url"] == normalized_url:
                    target["target_id"] = prior["target_id"]
                    if target_name is None:
                        target["target_name"] = prior["target_name"]
                    targets[index] = target
                    break
            else:
                targets.append(target)
            value = validate_config({"schema": CONFIG_SCHEMA, "targets": targets})
        else:
            value = validate_config({"schema": CONFIG_SCHEMA, "targets": [target]})
    except (TargetConfigError, ModelInputError) as error:
        raise SeoEmployeeError("SEO Employee configuration is invalid") from error
    atomic_write_json(config_path, value)
    return value


def load_configuration(config_path: Path = CONFIG_PATH, *, persist_migration: bool = True) -> dict[str, object]:
    try:
        value = _read_json_object(config_path)
        if value is None:
            raise TargetConfigError("config is invalid")
        if value.get("schema") == "extella.seo_employee_config.v1":
            return migrate_config_file(config_path) if persist_migration else migrate_config(value)
        return validate_config(value)
    except (OSError, TargetConfigError) as error:
        raise SeoEmployeeError("SEO Employee configuration is invalid") from error


def _worker_plan(plan: AuditPlan) -> dict[str, object]:
    return {
        "max_pages": plan.max_pages,
        "categories": list(plan.categories),
        "performance_sample_pages": plan.performance_sample_pages,
        "timeout_ms": plan.source_timeout_seconds * 1000,
    }


def _run_v2_source_wrapper(
    source: str,
    executable: Path,
    site_url: str,
    output_path: Path,
    plan: AuditPlan,
    *,
    runner: Callable[..., object],
    obtained_at: str,
    timeout_seconds: float,
) -> tuple[dict[str, object], SourceResult]:
    adapter = CrawlSEOAdapter() if source == "CrawlSEO" else SEOmatorAdapter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent, prefix=".plan.", suffix=".json", delete=False) as handle:
            plan_path = Path(handle.name)
            os.chmod(plan_path, 0o600)
            json.dump(_worker_plan(plan), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            completed = runner(
                [str(executable), site_url, str(plan_path), str(output_path)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            result = SourceResult(source, "unavailable", adapter.parse({"status": "unavailable", "reason": "timeout"}, plan).coverage, reason="timeout")
            return _source_status(source, result.status, obtained_at, reason=result.reason, coverage=result.coverage.as_dict()), result
        except (OSError, subprocess.SubprocessError):
            result = SourceResult(source, "unavailable", adapter.parse({"status": "unavailable", "reason": "waf"}, plan).coverage, reason="wrapper_unavailable")
            return _source_status(source, result.status, obtained_at, reason=result.reason, coverage=result.coverage.as_dict()), result
        if getattr(completed, "returncode", 1) != 0:
            result = SourceResult(source, "failed", adapter.parse({"status": "failed", "reason": "audit_failed"}, plan).coverage, reason="audit_failed")
            return _source_status(source, result.status, obtained_at, reason=result.reason, coverage=result.coverage.as_dict()), result
        result = _parse_source_payload(source, adapter, _read_json_object(output_path), plan)
        status = _source_status(source, result.status, obtained_at, reason=result.reason, coverage=result.coverage.as_dict())
        return status, result
    finally:
        if plan_path is not None:
            try:
                plan_path.unlink()
            except FileNotFoundError:
                pass


def collect_sources(
    site_url: str,
    run_id: str,
    *,
    plan: AuditPlan | None = None,
    evidence_dir: Path = EVIDENCE_DIR,
    runner: Callable[..., object] = subprocess.run,
    crawlseo_executable: Path = CRAWLSEO_EXECUTABLE,
    seomator_executable: Path = SEOMATOR_EXECUTABLE,
    obtained_at: str | None = None,
    deadline: RunDeadline | None = None,
) -> tuple[list[dict[str, object]], dict[str, SourceResult]]:
    selected_plan = plan or build_audit_plan("service_b2b", requested_max_pages=1)
    timestamp = obtained_at or _utc_now()
    budget = deadline or RunDeadline(selected_plan.overall_timeout_seconds)
    run_dir = evidence_dir / run_id
    definitions = (("CrawlSEO", crawlseo_executable, "crawlseo.json"), ("SEOmator", seomator_executable, "seomator.json"))
    statuses: list[dict[str, object]] = []
    results: dict[str, SourceResult] = {}
    for source, executable, filename in definitions:
        status, result = _run_v2_source_wrapper(
            source, executable, site_url, run_dir / filename, selected_plan, runner=runner,
            obtained_at=timestamp, timeout_seconds=budget.remaining(selected_plan.source_timeout_seconds),
        )
        statuses.append(status)
        results[source] = result
    for source in selected_plan.optional_sources:
        status = _optional_source_status(source, timestamp, selected_plan)
        statuses.append(status)
    return statuses, results


def _optional_source_status(source: str, obtained_at: str, plan: AuditPlan) -> dict[str, object]:
    coverage = {
        "planned_pages": plan.max_pages,
        "crawled_pages": 0,
        "sampled_pages": 0,
        "categories": list(plan.categories),
        "completed_sources": [],
        "unavailable_sources": [source],
        "unmapped_rules": [],
    }
    return _source_status(source, "not_configured", obtained_at, reason="not_configured", coverage=coverage)


def _failed_source_result(source: str, plan: AuditPlan) -> SourceResult:
    return SourceResult(
        source=source,
        status="failed",
        coverage=Coverage(
            planned_pages=plan.max_pages,
            crawled_pages=0,
            sampled_pages=0,
            categories=tuple(plan.categories),
            completed_sources=(),
            unavailable_sources=(),
            unmapped_rules=(),
        ),
        reason="invalid_payload",
    )


def _parse_source_payload(
    source: str,
    adapter: CrawlSEOAdapter | SEOmatorAdapter,
    payload: Mapping[str, object] | None,
    plan: AuditPlan,
) -> SourceResult:
    try:
        return adapter.parse(payload or {}, plan)
    except (ValueError, TypeError, KeyError, AttributeError):
        return _failed_source_result(source, plan)


def _bounded_fact(value: object) -> str:
    if not isinstance(value, str):
        return ""
    if _has_unsafe_controls(value):
        return ""
    normalized = " ".join(value.split())[:500]
    if not normalized or _SECRET_VALUE.search(normalized):
        return ""
    return normalized


def _has_unsafe_controls(value: str) -> bool:
    return any(
        (unicodedata.category(character) in {"Cc", "Cf"} and character not in _ALLOWED_FACT_WHITESPACE)
        or (character.isspace() and character not in _ALLOWED_FACT_WHITESPACE)
        for character in value
    )


def _normalize_dynamic_fact(value: object) -> str:
    """Return the one canonical safe representation accepted at the model boundary."""
    if not isinstance(value, str) or not value:
        raise ModelInputError("model input fact is invalid")
    if _has_unsafe_controls(value):
        raise ModelInputError("model input fact is invalid")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 500 or _SECRET_VALUE.search(normalized):
        raise ModelInputError("model input fact is invalid")
    return normalized


def _aggregate_coverage(plan: AuditPlan, results: Mapping[str, SourceResult]) -> dict[str, object]:
    values = list(results.values())
    return {
        "planned_pages": plan.max_pages,
        "crawled_pages": max((item.coverage.crawled_pages for item in values), default=0),
        "sampled_pages": max((item.coverage.sampled_pages for item in values), default=0),
        "categories": list(plan.categories),
        "completed_sources": sorted(item.source for item in values if item.status == "ok"),
        "unavailable_sources": sorted(item.source for item in values if item.status != "ok"),
        "unmapped_rules": sorted({rule for item in values for rule in item.coverage.unmapped_rules}),
    }


def normalize_v2_findings(
    target_id: str,
    plan: AuditPlan,
    payloads: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    adapters = {"CrawlSEO": CrawlSEOAdapter(), "SEOmator": SEOmatorAdapter()}
    results = {
        source: _parse_source_payload(source, adapter, payloads[source], plan)
        for source, adapter in adapters.items()
        if isinstance(payloads.get(source), Mapping)
    }
    return _normalize_v2_results(target_id, plan, results), _aggregate_coverage(plan, results)


def normalize_findings(target_id: str, payloads: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
    """Compatibility helper backed by the v2 adapters and catalog."""
    return normalize_v2_findings(target_id, build_audit_plan("service_b2b", requested_max_pages=1), payloads)[0]


def _normalize_v2_results(target_id: str, plan: AuditPlan, results: Mapping[str, SourceResult]) -> list[dict[str, object]]:
    merged: dict[tuple[str, str, str], dict[str, object]] = {}
    catalog = load_rule_catalog()
    for result in results.values():
        for occurrence in result.occurrences:
            definition = catalog.get(occurrence.rule_key)
            if definition is None or plan.profile not in definition.profiles:
                continue
            try:
                url = sanitize_url_for_model(occurrence.url)
            except ModelInputError:
                continue
            key = (target_id, url, occurrence.rule_key)
            finding = merged.setdefault(key, {
                "target_id": target_id,
                "url": url,
                "rule_key": occurrence.rule_key,
                "severity": definition.severity,
                "affected_pages_count": 1,
                "evidence": [],
                "confirmed_fact": definition.confirmed_fact or _bounded_fact(occurrence.fact),
                "verification": definition.verification or f"Repeat {occurrence.source} rule {occurrence.source_rule} and confirm it no longer fails.",
            })
            evidence = {"source": occurrence.source, "source_rule": occurrence.source_rule, "fact": _bounded_fact(occurrence.fact)}
            if evidence not in finding["evidence"]:
                finding["evidence"].append(evidence)
    findings: list[dict[str, object]] = []
    for finding in merged.values():
        definition = catalog[str(finding["rule_key"])]
        sources = [str(item["source"]) for item in finding["evidence"] if isinstance(item, Mapping)]
        finding["evidence"] = sorted(finding["evidence"], key=lambda item: (str(item["source"]), str(item["source_rule"]), str(item["fact"])))
        finding["evidence_level"] = evidence_level(definition, sources)
        if finding["evidence_level"] != "unverified" and str(finding["confirmed_fact"]):
            findings.append(finding)
    return findings


def _task_identity(task: Mapping[str, object]) -> str:
    return hashlib.sha256(f'{task["target_id"]}\0{task["url"]}\0{task["rule_key"]}'.encode("utf-8")).hexdigest()[:16]


def build_model_input(finding: Mapping[str, object]) -> dict[str, object]:
    value = {
        "rule_key": finding["rule_key"], "severity": finding["severity"], "evidence_level": finding["evidence_level"],
        "sources": sorted({str(item["source"]) for item in finding["evidence"] if isinstance(item, Mapping)}),
        "affected_pages_count": finding["affected_pages_count"], "confirmed_fact": finding["confirmed_fact"],
        "url": sanitize_url_for_model(str(finding["url"])),
    }
    validate_model_input(value)
    return value


def validate_model_input(value: Mapping[str, object]) -> None:
    if set(value) != MODEL_FIELDS:
        raise ModelInputError("model input fields do not match SC-SEO-031")
    definition = load_rule_catalog().get(str(value.get("rule_key")))
    if definition is None or value.get("severity") != definition.severity or value.get("evidence_level") not in {"verified", "supported"}:
        raise ModelInputError("model input is invalid")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources or sources != sorted(set(sources)) or any(source not in definition.source_rules for source in sources):
        raise ModelInputError("model input sources are invalid")
    if not isinstance(value.get("affected_pages_count"), int) or isinstance(value.get("affected_pages_count"), bool) or value["affected_pages_count"] <= 0:
        raise ModelInputError("model input page count is invalid")
    fact = _normalize_dynamic_fact(value.get("confirmed_fact"))
    if definition.confirmed_fact is not None and fact != definition.confirmed_fact:
        raise ModelInputError("model input fact is invalid")
    url = value.get("url")
    if not isinstance(url, str) or sanitize_url_for_model(url) != url:
        raise ModelInputError("model input URL is not sanitized")


def _not_compared() -> dict[str, object]:
    return {"baseline": "not_compared", "new": 0, "fixed": 0, "unchanged": 0, "new_items": [], "fixed_items": [], "unchanged_items": []}


def _comparison_for_v2(
    tasks: Sequence[Mapping[str, object]], baseline: Mapping[str, object] | None, *, target_id: str, plan: AuditPlan, terminal_state: str,
) -> tuple[dict[str, object], dict[str, object] | None]:
    if terminal_state == "failed":
        return _not_compared(), None
    if baseline is not None and (
        baseline.get("target_id") != target_id or baseline.get("plan_signature") != _plan_signature(plan) or baseline.get("catalog_major") != _catalog_major()
    ):
        return _not_compared(), None
    comparison, next_baseline = compare_with_baseline(tasks, baseline)
    if terminal_state == "partial":
        comparison["fixed"] = 0
        comparison["fixed_items"] = []
        return comparison, None
    next_baseline.update({"schema": "extella.seo_employee_baseline.v2", "target_id": target_id, "plan_signature": _plan_signature(plan), "catalog_major": _catalog_major()})
    return comparison, next_baseline


def _source_statuses_from_results(
    plan: AuditPlan,
    results: Mapping[str, SourceResult],
    *,
    obtained_at: str,
) -> list[dict[str, object]]:
    statuses = [
        _source_status(
            result.source,
            result.status,
            obtained_at,
            reason=result.reason,
            coverage=result.coverage.as_dict(),
        )
        for result in results.values()
    ]
    statuses.extend(_optional_source_status(source, obtained_at, plan) for source in plan.optional_sources)
    return statuses


def _action_proposal(
    task: Mapping[str, object], *, target_id: str, site_url: str, expires_at: str
) -> dict[str, object] | None:
    change = task.get("minimal_fix")
    if not isinstance(change, str) or not change or change != change.strip():
        return None
    proposal_seed = target_id + chr(0) + str(task["task_id"])
    proposal_id = "proposal-" + hashlib.sha256(proposal_seed.encode("utf-8")).hexdigest()[:16]
    return {
        "proposal_id": proposal_id,
        "target_id": target_id,
        "task_id": str(task["task_id"]),
        "target": {"target_id": target_id, "site_url": site_url},
        "operation": "manual_change",
        "change": change,
        "evidence": [dict(item) for item in task.get("evidence", []) if isinstance(item, Mapping)],
        "preview": change,
        "rollback": "Revert the manual change using the site's normal change history.",
        "expires_at": expires_at,
        "confirmation": "required",
        "status": "proposed",
    }


def _build_tasks(
    findings: Sequence[Mapping[str, object]],
    *,
    target_id: str,
    site_url: str,
    expires_at: str,
    enricher: Callable[[Mapping[str, object]], Mapping[str, str]],
    deadline: RunDeadline | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    tasks: list[dict[str, object]] = []
    enriched_count = 0
    failed = False
    for finding in findings:
        task: dict[str, object] = {"task_id": _task_identity(finding), **finding}
        try:
            model_input = build_model_input(finding)
            enriched = (
                enrich_with_agent_zero(model_input, timeout_seconds=deadline.remaining(AGENT_ZERO_TIMEOUT_SECONDS))
                if enricher is enrich_with_agent_zero and deadline is not None
                else _validate_enrichment(enricher(model_input))
            )
            task.update(enriched)
            enriched_count += 1
        except (SeoEmployeeError, OSError, RuntimeError, ImportError):
            failed = True
        proposal = _action_proposal(task, target_id=target_id, site_url=site_url, expires_at=expires_at)
        if proposal is not None:
            task["action_proposal"] = proposal
        tasks.append(task)
    total = len(tasks)
    if total == 0:
        return tasks, {"status": "not_needed", "limitation": "No deterministic findings require explanation.", "enriched": 0, "total": 0}
    if failed:
        return tasks, {
            "status": "unavailable",
            "limitation": "One or more model enrichments are unavailable; deterministic evidence is preserved.",
            "enriched": enriched_count,
            "total": total,
        }
    return tasks, {"status": "ok", "limitation": "", "enriched": enriched_count, "total": total}


def _mode_result(plan: AuditPlan, results: Mapping[str, SourceResult]) -> dict[str, object]:
    if plan.mode is AuditMode.SEARCH_PERFORMANCE:
        crawlseo = results.get("CrawlSEO")
        if crawlseo is not None and crawlseo.mode_result is not None:
            return dict(crawlseo.mode_result)
        return {
            "status": "not_configured",
            "reason": "not_configured",
            "next_action": "Connect Google Search Console in CrawlSEO.",
        }
    return {"status": "completed"}


def _build_v2_report(
    *,
    target: Mapping[str, object],
    plan: AuditPlan,
    command: Mapping[str, str],
    run_id: str,
    started_at: str,
    completed_at: str,
    results: Mapping[str, SourceResult],
    baseline: Mapping[str, object] | None,
    enricher: Callable[[Mapping[str, object]], Mapping[str, str]],
    deadline: RunDeadline | None = None,
) -> tuple[dict[str, object], dict[str, object] | None]:
    target_id = str(target["target_id"])
    findings = prioritize_findings(_normalize_v2_results(target_id, plan, results))
    tasks, model_status = _build_tasks(
        findings,
        target_id=target_id,
        site_url=command["site_url"],
        expires_at=_iso(datetime.fromisoformat(completed_at.replace("Z", "+00:00")) + timedelta(days=7)),
        enricher=enricher,
        deadline=deadline,
    )
    required_ok = required_sources_satisfied(plan, list(results.values()))
    any_factual = any(item.status == "ok" for item in results.values())
    state = "ready" if required_ok else "partial" if any_factual else "failed"
    comparison, next_baseline = _comparison_for_v2(
        tasks, baseline, target_id=target_id, plan=plan, terminal_state=state
    )
    report: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "normalizer_version": NORMALIZER_VERSION,
        "target": dict(target),
        "plan": _plan_public(plan),
        "run": {**command, "run_id": run_id, "started_at": started_at, "completed_at": completed_at},
        "state": state,
        "sources": _source_statuses_from_results(plan, results, obtained_at=completed_at),
        "coverage": _aggregate_coverage(plan, results),
        "mode_result": _mode_result(plan, results),
        "model_enrichment": model_status,
        "missing_data": [item.source for item in results.values() if item.status != "ok"],
        "comparison": comparison,
        "tasks": tasks,
    }
    if state == "failed":
        report["error"] = _safe_failure("SEO_SOURCES_UNAVAILABLE")
    return report, next_baseline


def normalize_one_verified_finding(
    target_id: str,
    crawlseo: Mapping[str, object],
    seomator: Mapping[str, object],
) -> dict[str, object]:
    plan = build_audit_plan("service_b2b", requested_max_pages=1)
    findings = _normalize_v2_results(target_id, plan, {
        "CrawlSEO": CrawlSEOAdapter().parse(crawlseo, plan),
        "SEOmator": SEOmatorAdapter().parse(seomator, plan),
    })
    verified = [finding for finding in findings if finding.get("evidence_level") == "verified"]
    if len(verified) != 1:
        raise SeoEmployeeError("the versioned rule pair is not confirmed by both sources")
    return verified[0]


def _next_scheduled_run(config: Mapping[str, object] | None, checked_at: str) -> str | None:
    if not isinstance(config, Mapping):
        return None
    if isinstance(config, Mapping) and config.get("schema") == CONFIG_SCHEMA:
        try:
            config = _target_from_config(config, None)
        except SeoEmployeeError:
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


def run_seo_employee(
    *,
    site_url: str,
    target_id: str | None = None,
    trigger: str = "manual",
    mode: str | None = None,
    config_path: Path = CONFIG_PATH,
    state_path: Path = STATE_PATH,
    report_path: Path = REPORT_PATH,
    history_dir: Path = HISTORY_DIR,
    evidence_dir: Path = EVIDENCE_DIR,
    baseline_path: Path = BASELINE_PATH,
    daily_index_path: Path = DAILY_INDEX_PATH,
    lock_dir: Path = LOCK_DIR,
    resolver: Callable[..., Iterable[tuple[Any, ...]]] = PUBLIC_RESOLVER,
    process_runner: Callable[..., object] = subprocess.run,
    enricher: Callable[[Mapping[str, object]], Mapping[str, str]] = enrich_with_agent_zero,
    now_provider: Callable[[], datetime] | None = None,
    crawlseo_executable: Path = CRAWLSEO_EXECUTABLE,
    seomator_executable: Path = SEOMATOR_EXECUTABLE,
) -> dict[str, object]:
    config = load_configuration(config_path, persist_migration=False)
    target = _target_from_config(config, target_id)
    if target.get("ownership_confirmed") is not True:
        raise SeoEmployeeError("ownership_confirmation_required")
    raw_config = _read_json_object(config_path)
    if isinstance(raw_config, Mapping) and raw_config.get("schema") == "extella.seo_employee_config.v1":
        config = migrate_config_file(config_path)
        target = _target_from_config(config, target_id)
    effective_url = site_url.strip() or str(target["site_url"])
    if effective_url != target["site_url"]:
        raise SeoEmployeeError("site_url does not match the configured target")
    now = _now_utc(now_provider)
    command = validate_run_command(
        {
            "target_id": str(target["target_id"]),
            "site_url": effective_url,
            "profile": str(target["profile"]),
            "mode": str(mode or target["mode"]),
            "trigger": trigger,
            "requested_at": _iso(now),
        },
        resolver=resolver,
    )
    terminal_error: dict[str, str] | None = None
    try:
        selected_mode = AuditMode(mode or str(target["mode"]))
        plan = build_audit_plan(str(target["profile"]), requested_max_pages=int(target["max_pages"]), mode=selected_mode)
    except (ValueError, TypeError) as error:
        raise SeoEmployeeError("mode is invalid") from error
    scoped = target_paths(_storage_root(config_path), command["target_id"])
    # No caller-provided shared paths participate in v2 target state.
    scoped_state, scoped_report = scoped["state"], scoped["report"]
    scoped_baseline, scoped_daily, scoped_locks = scoped["baseline"], scoped["daily_index"], scoped["locks"]
    scoped_history, scoped_evidence = scoped["history"], scoped["evidence"]
    deadline = RunDeadline(plan.overall_timeout_seconds)
    run_id = new_run_id()
    lock_path = _lock_path(scoped_locks, command["target_id"])
    acquired, active_run_id = _acquire_lock(lock_path, run_id, command["target_id"])
    if not acquired:
        return {"status": "success", "state": "duplicate", "run_id": active_run_id, "duplicate": True}
    try:
        if trigger == "daily":
            prior = completed_daily_run_id(target, now, config_path)
            if prior:
                return {"status": "success", "state": "duplicate", "run_id": prior, "duplicate": True}
        started_at = _iso(now)
        atomic_write_json(scoped_state, make_state("running", checked_at=started_at, config=target, run_id=run_id, trigger=trigger, last_report=_safe_read_json(scoped_report)))
        statuses, results = collect_sources(command["site_url"], run_id, plan=plan, evidence_dir=scoped_evidence, runner=process_runner, crawlseo_executable=crawlseo_executable, seomator_executable=seomator_executable, obtained_at=started_at, deadline=deadline)
        completed_at = _iso(_now_utc(now_provider))
        baseline = _safe_read_json(scoped_baseline)
        report, next_baseline = _build_v2_report(target=target, plan=plan, command=command, run_id=run_id, started_at=started_at, completed_at=completed_at, results=results, baseline=baseline, enricher=enricher, deadline=deadline)
        result_state = str(report["state"])
        try:
            _save_completed_report(report, report_path=scoped_report, history_dir=scoped_history)
        except Exception as error:
            terminal_error = _safe_failure("SEO_REPORT_SAVE_FAILED")
            raise SeoEmployeeError("report persistence failed") from error
        if trigger == "daily":
            _record_completed_daily_run(scoped_daily, _daily_key(command["target_id"], now, str(target["timezone"])), run_id, result_state)
        atomic_write_json(scoped_state, make_state(result_state, checked_at=completed_at, config=target, run_id=run_id, trigger=trigger, last_report=report, last_error=report.get("error") if isinstance(report.get("error"), Mapping) else None))
        if result_state == "ready" and next_baseline is not None:
            atomic_write_json(scoped_baseline, next_baseline)
        return {"status": "success" if result_state == "ready" else result_state, "state": result_state, "run_id": run_id, "report": report}
    except Exception:
        error = terminal_error or _safe_failure("SEO_RUN_FAILED")
        try:
            atomic_write_json(scoped_state, make_state("failed", checked_at=_iso(_now_utc(now_provider)), config=target, run_id=run_id, trigger=trigger, last_report=None, last_error=error))
        except Exception:
            pass
        return {"status": "failed", "state": "failed", "run_id": run_id, "error": error}
    finally:
        _release_lock(lock_path, run_id)


def complete_from_evidence(
    command: Mapping[str, object], *, crawlseo_path: Path, seomator_path: Path,
    report_path: Path, state_path: Path, config_path: Path | None = None,
    target_id: str | None = None, resolver: Callable[..., Iterable[tuple[Any, ...]]] = PUBLIC_RESOLVER,
    enricher: Callable[[Mapping[str, object]], Mapping[str, str]] = enrich_with_agent_zero,
) -> dict[str, object]:
    """Build a complete v2 report from already collected, local source evidence."""
    raw = dict(command)
    if config_path is not None:
        config = load_configuration(config_path)
        target = _target_from_config(config, target_id)
    else:
        site_url = validate_public_url(raw.get("site_url"), resolver=resolver)
        config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": site_id_from_url(site_url), "site_url": site_url, "daily_run_time": "00:00", "timezone": "UTC"})
        target = _target_from_config(config, None)
        target["ownership_confirmed"] = True
        config = validate_config({"schema": CONFIG_SCHEMA, "targets": [target]})
    site_url = validate_public_url(raw.get("site_url"), resolver=resolver)
    if site_url != target["site_url"]:
        raise SeoEmployeeError("site_url does not match the configured target")
    if target.get("ownership_confirmed") is not True:
        raise SeoEmployeeError("ownership_confirmation_required")
    crawl, seo = _read_json_object(crawlseo_path), _read_json_object(seomator_path)
    if crawl is None or seo is None:
        raise SeoEmployeeError("source evidence must be JSON objects")
    if config_path is None and isinstance(crawl.get("requested_max_pages"), int):
        target["max_pages"] = crawl["requested_max_pages"]
        config = validate_config({"schema": CONFIG_SCHEMA, "targets": [target]})
        target = _target_from_config(config, None)
    try:
        plan = build_audit_plan(
            str(target["profile"]),
            requested_max_pages=int(target["max_pages"]),
            mode=AuditMode(raw.get("mode", target["mode"])),
        )
    except (TypeError, ValueError) as error:
        raise SeoEmployeeError("mode is invalid") from error
    results = {
        "CrawlSEO": _parse_source_payload("CrawlSEO", CrawlSEOAdapter(), crawl, plan),
        "SEOmator": _parse_source_payload("SEOmator", SEOmatorAdapter(), seo, plan),
    }
    stamp = _utc_now()
    command_v2 = validate_run_command(
        {
            "target_id": str(target["target_id"]),
            "site_url": site_url,
            "profile": str(target["profile"]),
            "mode": plan.mode.value,
            "trigger": raw.get("trigger", "manual"),
            "requested_at": raw.get("requested_at", stamp),
        },
        resolver=resolver,
    )
    report, _ = _build_v2_report(
        target=target,
        plan=plan,
        command=command_v2,
        run_id=new_run_id(),
        started_at=stamp,
        completed_at=stamp,
        results=results,
        baseline=None,
        enricher=enricher,
    )
    atomic_write_json(report_path, report)
    atomic_write_json(state_path, make_state(str(report["state"]), checked_at=stamp, config=target, run_id=str(report["run"]["run_id"]), trigger=str(command_v2["trigger"]), last_report=report, last_error=report.get("error") if isinstance(report.get("error"), Mapping) else None))
    return report
