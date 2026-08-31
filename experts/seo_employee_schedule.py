# description: One-shot daily scheduler entry for Extella SEO Employee.

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "config.json"


def seo_employee_schedule(
    *,
    config_path: Path = CONFIG_PATH,
    now_provider: Callable[[], datetime] | None = None,
    run: Callable[..., str] | None = None,
) -> str:
    """Enqueue every due target, isolating one target's validation/run failure."""
    try:
        try:
            from seo_employee_service import SeoEmployeeError, completed_daily_run_id, load_configuration
        except ModuleNotFoundError:
            from experts.seo_employee_service import SeoEmployeeError, completed_daily_run_id, load_configuration

        config = load_configuration(config_path)
    except (OSError, SeoEmployeeError):
        return json.dumps(
            {
                "status": "error",
                "error": {
                    "code": "SEO_SCHEDULE_INVALID",
                    "message_ru": "Суточное расписание не настроено.",
                    "message_en": "The daily schedule is not configured.",
                },
            },
            ensure_ascii=False,
        )
    now = (now_provider or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        return json.dumps(
            {
                "status": "error",
                "error": {
                    "code": "SEO_CLOCK_INVALID",
                    "message_ru": "Системные часы не содержат часовой пояс.",
                    "message_en": "The system clock has no timezone.",
                },
            },
            ensure_ascii=False,
        )
    if run is None:
        try:
            from seo_employee_run import seo_employee_run
        except ModuleNotFoundError:
            from experts.seo_employee_run import seo_employee_run

        run = seo_employee_run
    outcomes: list[dict[str, object]] = []
    for target in config["targets"]:
        target_id = str(target["target_id"])
        try:
            zone = ZoneInfo(str(target["timezone"]))
            hour, minute = (int(part) for part in str(target["daily_run_time"]).split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
            local = now.astimezone(zone)
            if (local.hour, local.minute) < (hour, minute):
                outcomes.append({"target_id": target_id, "state": "not_due"})
                continue
            if completed_daily_run_id(target, now, config_path):
                outcomes.append({"target_id": target_id, "state": "duplicate"})
                continue
            result = json.loads(run(method="run", target_id=target_id, trigger="daily"))
            if not isinstance(result, dict):
                raise ValueError
            outcomes.append({"target_id": target_id, "state": result.get("state", "failed")})
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError, KeyError, ZoneInfoNotFoundError, json.JSONDecodeError):
            outcomes.append({"target_id": target_id, "state": "failed", "error": "schedule_target_invalid"})
    return json.dumps({
        "status": "success",
        "state": "queued" if any(item["state"] == "queued" for item in outcomes) else "not_due",
        "targets": outcomes,
    }, ensure_ascii=False)


def main() -> int:
    result = seo_employee_schedule()
    print(result)
    try:
        return 1 if json.loads(result).get("status") == "error" else 0
    except (AttributeError, json.JSONDecodeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
