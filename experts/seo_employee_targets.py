"""Validated v2 target configuration and safe per-target path derivation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from experts.seo_employee_profiles import DEFAULT_MAX_PAGES, HARD_MAX_PAGES, AuditMode, IndustryProfile


CONFIG_SCHEMA = "extella.seo_employee_config.v2"
V1_CONFIG_SCHEMA = "extella.seo_employee_config.v1"
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_SUPPORTED_LANGUAGES = frozenset({"ru", "en"})
_SUPPORTED_REGIONS = frozenset(("GLOBAL AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW").split())
_TARGET_ID_RE = re.compile(r"^target-[a-z0-9][a-z0-9-]{2,95}$")


class TargetConfigError(ValueError):
    """Raised for unsafe or invalid v2 target configuration."""


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TargetConfigError(f"{field} must be a non-empty string")
    return value


def _normalize_site_url(value: object) -> str:
    raw = _string(value, "site_url")
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise TargetConfigError("site_url is invalid") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TargetConfigError("site_url must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise TargetConfigError("site_url must not contain userinfo")
    if port is not None and not 1 <= port <= 65535:
        raise TargetConfigError("site_url port is invalid")
    host = parsed.hostname.lower()
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def _target_id_for(site_url: str) -> str:
    parsed = urllib.parse.urlsplit(site_url)
    slug = re.sub(r"[^a-z0-9]+", "-", parsed.hostname or "site").strip("-") or "site"
    digest = hashlib.sha256(site_url.encode("utf-8")).hexdigest()[:8]
    return f"target-{slug[:56].rstrip('-')}-{digest}"


def _normalize_language(value: object) -> str:
    normalized = _string(value, "language").lower()
    if normalized not in _SUPPORTED_LANGUAGES:
        raise TargetConfigError("language must be one of: ru, en")
    return normalized


def _normalize_region(value: object) -> str:
    normalized = _string(value, "region").upper()
    if normalized not in _SUPPORTED_REGIONS:
        raise TargetConfigError("region must be GLOBAL or an ISO 3166-1 alpha-2 code")
    return normalized


def _validate_target(value: object, *, expected_target_id: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TargetConfigError("target must be an object")
    required = {
        "target_id", "target_name", "site_url", "profile", "language", "region", "site_type", "business_goal",
        "daily_run_time", "timezone", "max_pages", "ownership_confirmed", "mode",
    }
    if set(value) != required:
        raise TargetConfigError("target has unexpected fields")
    site_url = _normalize_site_url(value["site_url"])
    target_id = _string(value["target_id"], "target_id")
    if not _TARGET_ID_RE.fullmatch(target_id) or target_id != expected_target_id:
        raise TargetConfigError("target_id is invalid or unstable")
    try:
        profile = IndustryProfile(value["profile"])
        mode = AuditMode(value["mode"])
    except (TypeError, ValueError) as error:
        raise TargetConfigError("profile or mode is invalid") from error
    scheduled = _string(value["daily_run_time"], "daily_run_time")
    if not _TIME_RE.fullmatch(scheduled):
        raise TargetConfigError("daily_run_time must use HH:MM")
    timezone = _string(value["timezone"], "timezone")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise TargetConfigError("timezone must be an IANA timezone") from error
    max_pages = value["max_pages"]
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= HARD_MAX_PAGES:
        raise TargetConfigError("max_pages must be between 1 and 100")
    ownership = value["ownership_confirmed"]
    if not isinstance(ownership, bool):
        raise TargetConfigError("ownership_confirmed must be a boolean")
    return {
        "target_id": target_id,
        "target_name": _string(value["target_name"], "target_name"),
        "site_url": site_url,
        "profile": profile.value,
        "language": _normalize_language(value["language"]),
        "region": _normalize_region(value["region"]),
        "site_type": _string(value["site_type"], "site_type"),
        "business_goal": _string(value["business_goal"], "business_goal"),
        "daily_run_time": scheduled,
        "timezone": timezone,
        "max_pages": max_pages,
        "ownership_confirmed": ownership,
        "mode": mode.value,
    }


def validate_config(value: object) -> dict[str, object]:
    """Validate and normalize an explicit v2 configuration without filesystem access."""
    if not isinstance(value, dict) or set(value) != {"schema", "targets"} or value.get("schema") != CONFIG_SCHEMA:
        raise TargetConfigError("v2 config schema is invalid")
    targets = value["targets"]
    if not isinstance(targets, list) or not targets:
        raise TargetConfigError("v2 config must contain targets")
    normalized_urls = [_normalize_site_url(target.get("site_url")) if isinstance(target, dict) else None for target in targets]
    if any(site_url is None for site_url in normalized_urls):
        raise TargetConfigError("target must be an object")
    normalized = [
        _validate_target(
            target,
            expected_target_id=_target_id_for(site_url),
        )
        for target, site_url in zip(targets, normalized_urls, strict=True)
    ]
    ids = [str(target["target_id"]) for target in normalized]
    if len(set(ids)) != len(ids):
        raise TargetConfigError("target_id values must be unique")
    return {"schema": CONFIG_SCHEMA, "targets": normalized}


def migrate_config(value: object) -> dict[str, object]:
    """Idempotently convert v1's single target into a conservative v2 target."""
    if not isinstance(value, dict):
        raise TargetConfigError("config must be an object")
    if value.get("schema") == CONFIG_SCHEMA:
        return validate_config(value)
    required_v1 = {"schema", "site_id", "site_url", "daily_run_time", "timezone"}
    if value.get("schema") != V1_CONFIG_SCHEMA or set(value) != required_v1:
        raise TargetConfigError("v1 config schema is invalid")
    site_url = _normalize_site_url(value["site_url"])
    migrated = {
        "schema": CONFIG_SCHEMA,
        "targets": [{
            "target_id": _target_id_for(site_url),
            "target_name": urllib.parse.urlsplit(site_url).hostname or "site",
            "site_url": site_url,
            "profile": IndustryProfile.SERVICE_B2B.value,
            "language": "ru",
            "region": "GLOBAL",
            "site_type": "website",
            "business_goal": "organic_visibility",
            "daily_run_time": value["daily_run_time"],
            "timezone": value["timezone"],
            "max_pages": DEFAULT_MAX_PAGES,
            "ownership_confirmed": False,
            "mode": AuditMode.DAILY_MONITOR.value,
        }],
    }
    return validate_config(migrated)


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    _atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))


def migrate_config_file(path: Path) -> dict[str, object]:
    """Atomically replace a valid v1 config only after its v2 representation validates."""
    try:
        original_bytes = path.read_bytes()
        original = json.loads(original_bytes.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TargetConfigError("config file cannot be read") from error
    migrating_v1 = isinstance(original, dict) and original.get("schema") == V1_CONFIG_SCHEMA
    migrated = migrate_config(original)
    if migrating_v1:
        backup = Path(f"{path}.v1.backup")
        if backup.exists() and backup.read_bytes() != original_bytes:
            raise TargetConfigError("v1 backup does not match current source")
        if not backup.exists():
            _atomic_write_bytes(backup, original_bytes)
    _atomic_write_json(path, migrated)
    return migrated


def target_paths(root: Path, target_id: str) -> dict[str, Path]:
    """Return isolated target paths without creating them."""
    if not isinstance(target_id, str) or not _TARGET_ID_RE.fullmatch(target_id):
        raise TargetConfigError("target_id is invalid")
    root_path = Path(root)
    target_state = root_path / "state" / "targets" / target_id
    paths = {
        "state": target_state / "state.json",
        "baseline": target_state / "baseline.json",
        "daily_index": target_state / "daily_runs.json",
        "locks": target_state / "locks",
        "report": root_path / "reports" / target_id / "latest.json",
        "history": root_path / "history" / target_id,
        "evidence": root_path / "evidence" / target_id,
    }
    resolved_root = root_path.resolve()
    if any(resolved_root not in candidate.resolve().parents and candidate.resolve() != resolved_root for candidate in paths.values()):
        raise TargetConfigError("target paths escape root")
    return paths
