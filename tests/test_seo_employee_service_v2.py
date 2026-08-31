from __future__ import annotations

import ast
from collections import Counter
import json
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from experts import seo_employee_service as service
from experts import seo_employee_state as public_state
from experts.seo_employee_sources import Coverage, SourceResult
from experts.seo_employee_targets import migrate_config


def public_resolver(*_args: object, **_kwargs: object):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def now() -> datetime:
    return datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def v1_config() -> dict[str, object]:
    return {
        "schema": "extella.seo_employee_config.v1",
        "site_id": "example-com",
        "site_url": "https://example.com/",
        "daily_run_time": "21:00",
        "timezone": "Asia/Tashkent",
    }


def plan_payload(max_pages: int, source: str) -> dict[str, object]:
    categories = list(service.build_audit_plan("service_b2b", requested_max_pages=max_pages).categories)
    coverage = {
        "planned_pages": max_pages,
        "crawled_pages": max_pages,
        "sampled_pages": 0 if source == "CrawlSEO" else min(max_pages, 5),
        "categories": categories,
    }
    if source == "CrawlSEO":
        return {
            "schema": "extella.crawlseo_source.v1", "source": source, "tool": "run_crawl", "tool_calls": 1,
            "requested_max_pages": max_pages, "crawl": {"status": "COMPLETED", "maxPages": max_pages, "pagesFound": max_pages},
            "coverage": coverage,
            "issues": [{"type": "MISSING_DESCRIPTION", "url": "https://example.com/", "message": "Missing meta description"}],
        }
    return {
        "url": "https://example.com/", "crawledPages": max_pages, "coverage": coverage,
        "categoryResults": [
            {"categoryId": category, "results": ([
                {"ruleId": "core-description-present", "status": "fail", "message": "Description is missing."},
                {"ruleId": "core-canonical-present", "status": "fail", "message": "Canonical is missing."},
            ] if category == "core" else [])}
            for category in categories
        ],
    }


def runner(calls: list[list[str]], *, fail: set[str] = set()):
    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        plan = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        source = "CrawlSEO" if argv[0].endswith("run_crawlseo") else "SEOmator"
        if source in fail:
            return SimpleNamespace(returncode=1)
        Path(argv[3]).write_text(json.dumps(plan_payload(plan["max_pages"], source)), encoding="utf-8")
        return SimpleNamespace(returncode=0)
    return run


class ServiceV2Tests(unittest.TestCase):
    def test_search_performance_mode_uses_structured_crawlseo_result(self) -> None:
        plan = service.build_audit_plan("service_b2b", requested_max_pages=25, mode="search_performance")
        result = SourceResult(
            source="CrawlSEO",
            status="ok",
            coverage=Coverage(25, 25, 0, tuple(plan.categories), ("CrawlSEO",), (), ()),
            mode_result={"status": "ready", "period_days": 28, "metrics": {"current": {"clicks": 12}}},
        )
        self.assertEqual(service._mode_result(plan, {"CrawlSEO": result})["metrics"]["current"]["clicks"], 12)

    def configure(self, root: Path, *, ownership: bool = True, max_pages: int = 25) -> tuple[Path, str]:
        config_path = root / "config" / "config.json"
        config = migrate_config(v1_config())
        config["targets"][0].update({"ownership_confirmed": ownership, "max_pages": max_pages})
        service.atomic_write_json(config_path, config)
        return config_path, str(config["targets"][0]["target_id"])

    def test_load_migrates_v1_with_russian_global_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "config.json"
            service.atomic_write_json(path, v1_config())
            loaded = service.load_configuration(path)
            self.assertEqual(loaded["schema"], service.CONFIG_SCHEMA)
            self.assertEqual(loaded["targets"][0]["language"], "ru")
            self.assertEqual(loaded["targets"][0]["region"], "GLOBAL")
            self.assertTrue(Path(f"{path}.v1.backup").exists())

    def test_ownership_rejects_before_runner_or_target_file_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "config.json"
            original = json.dumps(v1_config(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            config_path.parent.mkdir(parents=True)
            config_path.write_bytes(original)
            target_id = str(migrate_config(v1_config())["targets"][0]["target_id"])
            calls: list[list[str]] = []
            with self.assertRaisesRegex(service.SeoEmployeeError, "ownership_confirmation_required"):
                service.run_seo_employee(
                    site_url="", target_id=target_id, config_path=config_path, process_runner=runner(calls), resolver=public_resolver,
                )
            self.assertEqual(calls, [])
            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse(Path(f"{config_path}.v1.backup").exists())
            self.assertFalse((root / "state").exists())
            self.assertFalse((root / "reports").exists())
            self.assertFalse((root / "evidence").exists())

    def test_plan_limits_and_exact_private_wrapper_protocol(self) -> None:
        for max_pages, timeout in ((1, 120000), (25, 720000), (100, 720000)):
            with self.subTest(max_pages=max_pages), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config_path, target_id = self.configure(root, max_pages=max_pages)
                calls: list[list[str]] = []
                result = service.run_seo_employee(
                    site_url="", target_id=target_id, config_path=config_path, process_runner=runner(calls), resolver=public_resolver, now_provider=now,
                )
                self.assertEqual(result["state"], "ready")
                self.assertEqual(len(calls), 2)
                for argv in calls:
                    self.assertEqual(len(argv), 4)
                    self.assertEqual(argv[1], "https://example.com/")
                    self.assertFalse(Path(argv[2]).exists())
                    self.assertEqual(Path(argv[3]).name, "crawlseo.json" if argv[0].endswith("run_crawlseo") else "seomator.json")
                self.assertEqual(result["report"]["plan"]["max_pages"], max_pages)
                self.assertEqual(result["report"]["plan"]["source_timeout_seconds"] * 1000, timeout)

    def test_known_rules_use_catalog_corroboration_and_unknown_rules_are_coverage_only(self) -> None:
        plan = service.build_audit_plan("service_b2b", requested_max_pages=1)
        crawl = plan_payload(1, "CrawlSEO")
        seo = plan_payload(1, "SEOmator")
        crawl["issues"].append({"type": "UNKNOWN", "url": "https://example.com/", "message": "ignored"})
        findings, coverage = service.normalize_v2_findings("target-example-com-0f115db0", plan, {"CrawlSEO": crawl, "SEOmator": seo})
        self.assertEqual(len(service.load_rule_catalog()), 251)
        self.assertTrue(all(service.canonical_rule(source, source_rule) is definition for definition in service.load_rule_catalog().values() for source, source_rule in definition.source_rules.items()))
        levels = {item["rule_key"]: item["evidence_level"] for item in findings}
        self.assertEqual(levels["meta-description-missing"], "verified")
        self.assertEqual(levels["core-canonical-present"], "supported")
        meta = next(item for item in findings if item["rule_key"] == "meta-description-missing")
        self.assertEqual(meta["confirmed_fact"], "На странице отсутствует meta description.")
        self.assertIn("UNKNOWN", coverage["unmapped_rules"])

    def test_optional_not_configured_and_model_failure_do_not_downgrade_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, target_id = self.configure(root)
            result = service.run_seo_employee(
                site_url="", target_id=target_id, config_path=config_path, process_runner=runner([]), resolver=public_resolver,
                enricher=lambda _value: (_ for _ in ()).throw(RuntimeError("offline")), now_provider=now,
            )
            self.assertEqual(result["state"], "ready")
            self.assertEqual(result["report"]["model_enrichment"]["status"], "unavailable")
            self.assertEqual(result["report"]["mode_result"]["status"], "completed")
            self.assertIn("GoogleSearchConsole", {item["name"] for item in result["report"]["sources"]})
            self.assertTrue(all("action_proposal" not in task for task in result["report"]["tasks"]))

    def test_partial_does_not_fix_or_update_baseline_and_incompatible_plan_is_not_compared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, target_id = self.configure(root)
            ready = service.run_seo_employee(site_url="", target_id=target_id, config_path=config_path, process_runner=runner([]), resolver=public_resolver, now_provider=now)
            paths = service.target_paths(root, target_id)
            before = paths["baseline"].read_bytes()
            partial = service.run_seo_employee(site_url="", target_id=target_id, config_path=config_path, process_runner=runner([], fail={"SEOmator"}), resolver=public_resolver, now_provider=now)
            self.assertEqual(ready["state"], "ready")
            self.assertEqual(partial["state"], "partial")
            self.assertEqual(partial["report"]["comparison"]["fixed"], 0)
            self.assertEqual(paths["baseline"].read_bytes(), before)
            config = service.load_configuration(config_path)
            config["targets"][0]["max_pages"] = 1
            service.atomic_write_json(config_path, config)
            changed = service.run_seo_employee(site_url="", target_id=target_id, config_path=config_path, process_runner=runner([]), resolver=public_resolver, now_provider=now)
            self.assertEqual(changed["report"]["comparison"]["baseline"], "not_compared")

    def test_target_storage_isolated_and_search_performance_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, first = self.configure(root)
            config = service.load_configuration(config_path)
            second = migrate_config({**v1_config(), "site_url": "https://example.org/"})["targets"][0]
            second["ownership_confirmed"] = True
            config["targets"].append(second)
            service.atomic_write_json(config_path, config)
            result = service.run_seo_employee(site_url="", target_id=second["target_id"], mode="search_performance", config_path=config_path, process_runner=runner([]), resolver=public_resolver, now_provider=now)
            first_paths = service.target_paths(root, first)
            second_paths = service.target_paths(root, second["target_id"])
            for name in ("state", "report", "baseline", "daily_index", "locks"):
                self.assertNotEqual(first_paths[name], second_paths[name])
            self.assertTrue(second_paths["report"].exists())
            self.assertEqual(result["state"], "ready")
            self.assertEqual(result["report"]["mode_result"]["status"], "not_configured")

    def test_invalid_required_source_is_partial_then_two_invalid_sources_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, target_id = self.configure(root)

            def invalid_runner(invalid: set[str]):
                def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
                    plan = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
                    source = "CrawlSEO" if argv[0].endswith("run_crawlseo") else "SEOmator"
                    payload = {"malformed": True} if source in invalid else plan_payload(plan["max_pages"], source)
                    Path(argv[3]).write_text(json.dumps(payload), encoding="utf-8")
                    return SimpleNamespace(returncode=0)
                return run

            partial = service.run_seo_employee(
                site_url="", target_id=target_id, config_path=config_path,
                process_runner=invalid_runner({"CrawlSEO"}), resolver=public_resolver, now_provider=now,
            )
            self.assertEqual(partial["state"], "partial")
            self.assertEqual({item["name"]: item["status"] for item in partial["report"]["sources"]}["CrawlSEO"], "failed")
            self.assertEqual(len(partial["report"]["tasks"]), 2)
            self.assertEqual(partial["report"]["tasks"][0]["rule_key"], "core-canonical-present")
            failed = service.run_seo_employee(
                site_url="", target_id=target_id, config_path=config_path,
                process_runner=invalid_runner({"CrawlSEO", "SEOmator"}), resolver=public_resolver, now_provider=now,
            )
            self.assertEqual(failed["state"], "failed")

    def test_non_ok_source_statuses_have_fixed_safe_messages_and_coverage(self) -> None:
        plan = service.build_audit_plan("service_b2b", requested_max_pages=1)
        result = service.CrawlSEOAdapter().parse({"malformed": True}, plan)
        status = service._source_status("CrawlSEO", result.status, "2026-08-30T12:00:00Z", reason="unexpected secret=abc", coverage=result.coverage.as_dict())
        self.assertEqual(status["reason"], "source_failed")
        self.assertEqual(set(("message_ru", "message_en", "instruction", "coverage")), set(status) & {"message_ru", "message_en", "instruction", "coverage"})
        self.assertNotIn("secret=abc", status["message_ru"])
        self.assertNotIn("secret=abc", status["message_en"])
        self.assertNotIn("secret=abc", status["instruction"])

    def test_model_boundary_rejects_dynamic_facts_over_limit_controls_and_secrets(self) -> None:
        for value in ("x" * 501, "bearer sk-abcdefgh"):
            with self.subTest(value=value[:12]):
                with self.assertRaises(service.ModelInputError):
                    service._normalize_dynamic_fact(value)

    def test_model_boundary_rejects_secret_key_value_forms_without_rejecting_ordinary_prose(self) -> None:
        for value in (
            "token=redacted",
            "PASSWORD: redacted",
            "cookie=redacted",
            "client_secret=redacted",
            "api-key=redacted",
            "access_token=redacted",
            "refresh_token=redacted",
        ):
            with self.subTest(value=value):
                with self.assertRaises(service.ModelInputError):
                    service._normalize_dynamic_fact(value)
        for value in (
            "The token field is not configured.",
            "A password policy is documented for editors.",
            "Cookie consent appears on the page.",
        ):
            with self.subTest(value=value):
                self.assertEqual(service._normalize_dynamic_fact(value), value)

    def test_model_boundary_rejects_unsafe_original_controls_but_collapses_ordinary_whitespace(self) -> None:
        for value in ("verified\x0bfact", "verified\x0cfact", "verified\u0085fact", "verified\x7ffact", "verified\x80fact", "verified\u200bfact"):
            with self.subTest(value=value.encode("unicode_escape")):
                with self.assertRaises(service.ModelInputError):
                    service._normalize_dynamic_fact(value)
        self.assertEqual(service._normalize_dynamic_fact("verified\t\n\r fact"), "verified fact")

    def test_service_has_no_duplicate_top_level_definitions_or_stale_proposal_fallback(self) -> None:
        source = (Path(__file__).parents[1] / "experts" / "seo_employee_service.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        self.assertEqual({name: count for name, count in Counter(names).items() if count > 1}, {})
        for fragment in (
            'task.get("minimal_fix") or task.get("verification")',
            "Apply the documented manual correction.",
            "_obsolete_",
        ):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, source)

    def test_complete_from_evidence_selects_target_then_rejects_cross_site_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, target_id = self.configure(root)
            crawl, seo = root / "crawl.json", root / "seo.json"
            report_path, state_path = root / "report.json", root / "state.json"
            crawl.write_text(json.dumps(plan_payload(25, "CrawlSEO")), encoding="utf-8")
            seo.write_text(json.dumps(plan_payload(25, "SEOmator")), encoding="utf-8")
            with self.assertRaisesRegex(service.SeoEmployeeError, "site_url does not match the configured target"):
                service.complete_from_evidence(
                    {"target_id": target_id, "site_url": "https://example.org/", "profile": "service_b2b", "mode": "daily_monitor", "trigger": "manual", "requested_at": "2026-08-30T12:00:00Z"},
                    crawlseo_path=crawl, seomator_path=seo, report_path=report_path, state_path=state_path,
                    config_path=config_path, target_id=target_id, resolver=public_resolver,
                )
            self.assertFalse(report_path.exists())
            self.assertFalse(state_path.exists())

    def test_action_proposal_requires_successful_nonempty_model_minimal_fix(self) -> None:
        finding = {
            "task_id": "task", "evidence": [], "verification": "Repeat the source check.",
        }
        self.assertIsNone(service._action_proposal(finding, target_id="target-example", site_url="https://example.com/", expires_at="2026-09-01T00:00:00Z"))
        finding["minimal_fix"] = "Fix the missing description manually."
        self.assertEqual(service._action_proposal(finding, target_id="target-example", site_url="https://example.com/", expires_at="2026-09-01T00:00:00Z")["change"], finding["minimal_fix"])

    def test_v2_command_and_public_state_select_one_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, first = self.configure(root)
            config = service.load_configuration(config_path)
            second = migrate_config({**v1_config(), "site_url": "https://example.org/"})["targets"][0]
            second.update({"ownership_confirmed": True, "daily_run_time": "09:30", "timezone": "UTC"})
            config["targets"].append(second)
            service.atomic_write_json(config_path, config)
            state_path = service.target_paths(root, second["target_id"])["state"]
            service.atomic_write_json(
                state_path,
                service.make_state("ready", checked_at="2026-08-30T12:00:00Z", config=config, run_id="seo-test", trigger="manual"),
            )
            value = json.loads(public_state.seo_employee_state(
                target_id=second["target_id"], state_path=state_path, config_path=config_path, now_provider=now,
            ))
            self.assertEqual(value["config"]["target_id"], second["target_id"])
            self.assertNotIn("targets", value["config"])
            self.assertEqual(value["schedules"][0]["next_run"], "2026-08-31T09:30:00+00:00")
            command = {
                "target_id": first, "site_url": "https://example.com/", "profile": "service_b2b",
                "mode": "daily_monitor", "trigger": "manual", "requested_at": "2026-08-30T12:00:00Z",
            }
            self.assertEqual(service.validate_run_command(command, resolver=public_resolver), command)
            with self.assertRaises(service.SeoEmployeeError):
                service.validate_run_command({**command, "site_id": "obsolete"}, resolver=public_resolver)

    def test_evidence_path_uses_full_v2_shape_and_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            crawl, seo = root / "crawl.json", root / "seo.json"
            report_path, state_path = root / "report.json", root / "state.json"
            crawl.write_text(json.dumps(plan_payload(25, "CrawlSEO")), encoding="utf-8")
            seo.write_text(json.dumps(plan_payload(25, "SEOmator")), encoding="utf-8")
            report = service.complete_from_evidence(
                {
                    "target_id": "ignored", "site_url": "https://example.com/", "profile": "service_b2b",
                    "mode": "daily_monitor", "trigger": "manual", "requested_at": "2026-08-30T12:00:00Z",
                },
                crawlseo_path=crawl, seomator_path=seo, report_path=report_path, state_path=state_path,
                resolver=public_resolver, enricher=lambda _value: {"business_impact": "Проверенный факт требует внимания.", "minimal_fix": "Исправить подтверждённую проблему вручную."},
            )
            self.assertTrue({"target", "plan", "sources", "coverage", "mode_result", "model_enrichment", "comparison", "tasks"}.issubset(report))
            self.assertTrue(all("coverage" in source for source in report["sources"]))
            proposal = report["tasks"][0]["action_proposal"]
            self.assertTrue({"proposal_id", "target_id", "task_id", "operation", "change", "evidence", "preview", "rollback", "expires_at", "confirmation", "status"}.issubset(proposal))

    def test_report_persistence_failure_leaves_terminal_failed_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, target_id = self.configure(root)
            with mock.patch.object(service, "_save_completed_report", side_effect=OSError("disk unavailable")):
                result = service.run_seo_employee(
                    site_url="", target_id=target_id, config_path=config_path, process_runner=runner([]),
                    resolver=public_resolver, now_provider=now,
                )
            self.assertEqual(result["error"]["code"], "SEO_REPORT_SAVE_FAILED")
            state = json.loads(service.target_paths(root, target_id)["state"].read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "failed")
            self.assertEqual(state["last_error"]["code"], "SEO_REPORT_SAVE_FAILED")

    def test_mixed_model_result_stays_unavailable_with_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, target_id = self.configure(root)
            calls = 0
            def mixed(_value: object) -> dict[str, str]:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("offline")
                return {"business_impact": "Проверенный факт требует внимания.", "minimal_fix": "Исправить подтверждённую проблему вручную."}
            report = service.run_seo_employee(
                site_url="", target_id=target_id, config_path=config_path, process_runner=runner([]),
                resolver=public_resolver, now_provider=now, enricher=mixed,
            )["report"]
            self.assertEqual(report["model_enrichment"]["status"], "unavailable")
            self.assertEqual(report["model_enrichment"]["enriched"], 1)
            self.assertEqual(report["model_enrichment"]["total"], 2)

    def test_all_profiles_run_with_exact_worker_plan_keys_and_ten_task_cap(self) -> None:
        profiles = ("service_b2b", "ecommerce", "local_business", "content_media", "saas_marketplace")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, target_id = self.configure(root)
            worker_plans: list[dict[str, object]] = []

            def profile_runner(argv: list[str], **_kwargs: object) -> SimpleNamespace:
                plan = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
                worker_plans.append(plan)
                categories = plan["categories"]
                limit = plan["max_pages"]
                source = "CrawlSEO" if argv[0].endswith("run_crawlseo") else "SEOmator"
                if source == "CrawlSEO":
                    payload = {
                        "schema": "extella.crawlseo_source.v1", "source": source, "tool": "run_crawl", "tool_calls": 1,
                        "requested_max_pages": limit, "crawl": {"status": "COMPLETED", "maxPages": limit, "pagesFound": limit},
                        "coverage": {"planned_pages": limit, "crawled_pages": limit, "sampled_pages": 0, "categories": categories},
                        "issues": [{"type": "MISSING_DESCRIPTION", "url": f"https://example.com/{index}", "message": "Missing meta description"} for index in range(12)],
                    }
                else:
                    payload = {
                        "url": "https://example.com/", "crawledPages": limit,
                        "coverage": {"planned_pages": limit, "crawled_pages": limit, "sampled_pages": min(limit, 5), "categories": categories},
                        "categoryResults": [{"categoryId": category, "results": ([{"ruleId": "core-description-present", "status": "fail", "message": "Description is missing."}] if category == "core" else [])} for category in categories],
                    }
                Path(argv[3]).write_text(json.dumps(payload), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            for profile in profiles:
                config = service.load_configuration(config_path)
                config["targets"][0]["profile"] = profile
                service.atomic_write_json(config_path, config)
                result = service.run_seo_employee(
                    site_url="", target_id=target_id, config_path=config_path, process_runner=profile_runner,
                    resolver=public_resolver, now_provider=now,
                )
                self.assertEqual(result["state"], "ready")
                self.assertEqual(len(result["report"]["tasks"]), 10)
                self.assertEqual(
                    [item["url"] for item in result["report"]["tasks"]],
                    ["https://example.com/", "https://example.com/0", "https://example.com/1", "https://example.com/10", "https://example.com/11"]
                    + [f"https://example.com/{index}" for index in range(2, 7)],
                )
            self.assertEqual(len(worker_plans), 10)
            self.assertTrue(all(set(plan) == {"max_pages", "categories", "performance_sample_pages", "timeout_ms"} for plan in worker_plans))

    def test_multi_target_upsert_preserves_target_name_and_other_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "config.json"
            service.save_configuration(
                "https://example.com/", "21:00", "UTC", target_name="Primary",
                ownership_confirmed=True, config_path=path, resolver=public_resolver,
            )
            created = service.save_configuration(
                "https://example.org/", "09:30", "UTC", target_name="Secondary",
                ownership_confirmed=True, config_path=path, resolver=public_resolver,
            )
            secondary = next(item for item in created["targets"] if item["site_url"] == "https://example.org/")
            updated = service.save_configuration(
                "https://example.com/", "22:00", "UTC", ownership_confirmed=True,
                config_path=path, resolver=public_resolver,
            )
            primary = next(item for item in updated["targets"] if item["site_url"] == "https://example.com/")
            retained = next(item for item in updated["targets"] if item["site_url"] == "https://example.org/")
            self.assertEqual(primary["target_name"], "Primary")
            self.assertEqual(primary["daily_run_time"], "22:00")
            self.assertEqual(retained, secondary)


if __name__ == "__main__":
    unittest.main()
