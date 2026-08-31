from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SERVICE = _load("seo_employee_service", ROOT / "experts" / "seo_employee_service.py")
STATE = _load("seo_employee_state", ROOT / "experts" / "seo_employee_state.py")
RUN = _load("seo_employee_run", ROOT / "experts" / "seo_employee_run.py")
SCHEDULE = _load("seo_employee_schedule", ROOT / "experts" / "seo_employee_schedule.py")
SERVER = _load("seo_employee_server", ROOT / "runtime" / "product" / "server.py")


def _public_resolver(*_args: object, **_kwargs: object):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _crawlseo() -> dict[str, object]:
    categories = list(SERVICE.build_audit_plan("service_b2b", requested_max_pages=1).categories)
    return {
        "schema": "extella.crawlseo_source.v1",
        "source": "CrawlSEO",
        "source_commit": "8683b2740eca5059faa0949c2175a7548216bd50",
        "tool": "run_crawl",
        "tool_calls": 1,
        "requested_max_pages": 1,
        "crawl": {
            "status": "COMPLETED",
            "maxPages": 1,
            "pagesFound": 1,
        },
        "coverage": {"planned_pages": 1, "crawled_pages": 1, "sampled_pages": 0, "categories": categories},
        "issues": [
            {
                "type": "MISSING_DESCRIPTION",
                "severity": "WARNING",
                "url": "https://example.com/",
                "message": "Missing meta description",
            },
            {
                "type": "MISSING_SCHEMA",
                "severity": "INFO",
                "url": "https://example.com/",
                "message": "No structured data",
            },
        ],
    }


def _seomator() -> dict[str, object]:
    categories = list(SERVICE.build_audit_plan("service_b2b", requested_max_pages=1).categories)
    return {
        "url": "https://example.com/",
        "crawledPages": 1,
        "coverage": {"planned_pages": 1, "crawled_pages": 1, "sampled_pages": 1, "categories": categories},
        "categoryResults": [
            {
                "categoryId": "core",
                "results": [
                    {
                        "ruleId": "core-description-present",
                        "status": "fail",
                        "message": 'No <meta name="description"> tag found in the document',
                        "score": 0,
                    },
                    {
                        "ruleId": "core-canonical-present",
                        "status": "fail",
                        "message": 'No <link rel="canonical"> tag found in the document',
                        "score": 0,
                    },
                ],
            }
        ] + [{"categoryId": category, "results": []} for category in categories if category != "core"],
    }


def _command() -> dict[str, str]:
    return {
        "target_id": "target-example-com-0f115db0",
        "site_url": "https://example.com/",
        "profile": "service_b2b",
        "mode": "daily_monitor",
        "trigger": "manual",
        "requested_at": "2026-08-29T18:00:00Z",
    }


def _fixed_now() -> datetime:
    return datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)


def _enrich(_value: dict[str, object]) -> dict[str, str]:
    return {
        "business_impact": "Сниппет страницы может быть менее понятным в поисковой выдаче.",
        "minimal_fix": "Добавить уникальное meta description по содержанию страницы.",
    }


def _service_paths(root: Path) -> dict[str, object]:
    scoped = SERVICE.target_paths(root, "target-example-com-0f115db0")
    return {
        "config_path": root / "config" / "config.json",
        "state_path": scoped["state"],
        "report_path": scoped["report"],
        "history_dir": scoped["history"],
        "evidence_dir": scoped["evidence"],
        "baseline_path": scoped["baseline"],
        "daily_index_path": scoped["daily_index"],
        "lock_dir": scoped["locks"],
        "resolver": _public_resolver,
        "enricher": _enrich,
        "now_provider": _fixed_now,
        "crawlseo_executable": Path("/opt/extella-seo-employee/runtime/run_crawlseo"),
        "seomator_executable": Path("/opt/extella-seo-employee/runtime/run_seomator"),
    }


def _configure(paths: dict[str, object]) -> None:
    SERVICE.save_configuration(
        "https://example.com/",
        "21:00",
        "Asia/Tashkent",
        config_path=paths["config_path"],
        resolver=_public_resolver,
        ownership_confirmed=True,
        max_pages=1,
    )


def _source_runner(
    calls: list[list[str]],
    *,
    crawl: dict[str, object] | None = None,
    seomator: dict[str, object] | None = None,
    failures: set[str] | None = None,
):
    failures = failures or set()

    def run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        executable, _url, _plan, output = args
        name = "CrawlSEO" if executable.endswith("run_crawlseo") else "SEOmator"
        self_payload = (crawl or _crawlseo()) if name == "CrawlSEO" else (seomator or _seomator())
        if name in failures:
            return SimpleNamespace(returncode=1)
        Path(output).write_text(json.dumps(self_payload), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    return run


class SeoEmployeeContractTest(unittest.TestCase):
    def test_ct_sc_001_validates_manual_command_run_id_and_public_url(self) -> None:
        self.assertEqual(
            SERVICE.validate_run_command(_command(), resolver=_public_resolver)["trigger"],
            "manual",
        )
        self.assertNotEqual(SERVICE.new_run_id(), SERVICE.new_run_id())
        bad = {**_command(), "site_url": "http://127.0.0.1/private"}
        with self.assertRaisesRegex(SERVICE.SeoEmployeeError, "public addresses"):
            SERVICE.validate_run_command(bad)

    def test_ct_sc_002_validates_schedule_and_deduplicates_daily_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _service_paths(root)
            _configure(paths)
            calls: list[list[str]] = []
            paths["process_runner"] = _source_runner(calls)
            first = SERVICE.run_seo_employee(site_url="", trigger="daily", **paths)
            second = SERVICE.run_seo_employee(site_url="", trigger="daily", **paths)
            self.assertEqual(first["state"], "ready")
            self.assertTrue(second["duplicate"])
            self.assertEqual(second["run_id"], first["run_id"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(list(paths["lock_dir"].glob("*.lock")), [])

            state = SERVICE.make_state("ready", checked_at="2026-08-29T18:00:00Z", config=SERVICE.load_configuration(paths["config_path"]))
            self.assertEqual(state["schedules"][0]["next_run"], "2026-08-30T21:00:00+05:00")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SERVICE.SeoEmployeeError, "HH:MM"):
                SERVICE.save_configuration(
                    "https://example.com/",
                    "24:00",
                    "UTC",
                    config_path=Path(directory) / "config.json",
                    resolver=_public_resolver,
                )

    def test_ct_sc_003_returns_existing_active_run_and_keeps_its_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _service_paths(root)
            _configure(paths)
            target_id = "target-example-com-0f115db0"
            lock_path = SERVICE._lock_path(paths["lock_dir"], target_id)
            SERVICE.atomic_write_json(
                lock_path,
                {
                    "run_id": "seo-existing",
                    "site_id": target_id,
                    "instance_id": SERVICE.SERVICE_INSTANCE_ID,
                },
            )
            calls: list[list[str]] = []
            paths["process_runner"] = _source_runner(calls)
            result = SERVICE.run_seo_employee(site_url="", **paths)
            self.assertEqual(result["run_id"], "seo-existing")
            self.assertEqual(result["state"], "duplicate")
            self.assertEqual(calls, [])
            self.assertTrue(lock_path.exists())

    def test_ct_sc_003_recovers_sigkill_lock_for_a_new_single_replica_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "run.lock"
            SERVICE.atomic_write_json(
                lock_path,
                {"run_id": "seo-killed", "site_id": "example.com", "instance_id": "dead-instance"},
            )
            acquired, run_id = SERVICE._acquire_lock(
                lock_path,
                "seo-recovered",
                "example.com",
                instance_id="new-instance",
            )
            self.assertTrue(acquired)
            self.assertEqual(run_id, "seo-recovered")
            self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8"))["instance_id"], "new-instance")

    def test_ct_sc_003_serializes_stale_lock_recovery_within_single_replica(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "run.lock"
            SERVICE.atomic_write_json(
                lock_path,
                {"run_id": "seo-killed", "site_id": "example.com", "instance_id": "dead-instance"},
            )
            stale_readers = threading.Barrier(2)
            original_read = SERVICE._safe_read_json
            results: list[tuple[bool, str]] = []

            def synchronized_stale_read(path: Path) -> dict[str, object] | None:
                value = original_read(path)
                if isinstance(value, dict) and value.get("instance_id") == "dead-instance":
                    try:
                        stale_readers.wait(timeout=0.2)
                    except threading.BrokenBarrierError:
                        pass
                return value

            def acquire(run_id: str) -> None:
                results.append(
                    SERVICE._acquire_lock(
                        lock_path,
                        run_id,
                        "example.com",
                        instance_id="new-instance",
                    )
                )

            with mock.patch.object(SERVICE, "_safe_read_json", side_effect=synchronized_stale_read):
                threads = [threading.Thread(target=acquire, args=(run_id,)) for run_id in ("seo-first", "seo-second")]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=1)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(len(results), 2)
            winners = [run_id for acquired, run_id in results if acquired]
            losers = [run_id for acquired, run_id in results if not acquired]
            self.assertEqual(len(winners), 1)
            self.assertEqual(losers, winners)

    def test_ct_sc_004_calls_local_wrappers_with_argument_arrays_and_tracks_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: list[tuple[list[str], dict[str, object]]] = []

            def runner(args: list[str], **kwargs: object) -> SimpleNamespace:
                calls.append((args, kwargs))
                payload = _crawlseo() if args[0].endswith("run_crawlseo") else _seomator()
                Path(args[3]).write_text(json.dumps(payload), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            statuses, payloads = SERVICE.collect_sources(
                "https://example.com/",
                "seo-test",
                evidence_dir=Path(directory),
                runner=runner,
            )
            self.assertEqual([item["status"] for item in statuses], ["ok", "ok", "not_configured", "not_configured"])
            self.assertEqual(set(payloads), {"CrawlSEO", "SEOmator"})
            self.assertEqual(
                [call[0][0] for call in calls],
                [
                    str(SERVICE.CRAWLSEO_EXECUTABLE),
                    str(SERVICE.SEOMATOR_EXECUTABLE),
                ],
            )
            self.assertTrue(all(len(call[0]) == 4 for call in calls))
            self.assertTrue(all("shell" not in call[1] for call in calls))
            self.assertTrue(all(call[1]["stdout"] is not None and call[1]["stderr"] is not None for call in calls))

    def test_ct_sc_004_rejects_source_outputs_that_exceed_one_page(self) -> None:
        crawl = _crawlseo()
        crawl["crawl"]["pagesFound"] = 2
        seo = _seomator()
        seo["crawledPages"] = 2
        self.assertFalse(SERVICE._source_payload_is_valid("CrawlSEO", crawl))
        self.assertFalse(SERVICE._source_payload_is_valid("SEOmator", seo))

    def test_ct_sc_005_normalizes_all_known_occurrences_merges_and_keeps_evidence(self) -> None:
        findings = SERVICE.normalize_findings("example-com", {"CrawlSEO": _crawlseo(), "SEOmator": _seomator()})
        verified = [item for item in findings if item["rule_key"] == "meta-description-missing"]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["evidence_level"], "verified")
        self.assertEqual(len(verified[0]["evidence"]), 2)
        self.assertEqual(
            {evidence["source"] for evidence in verified[0]["evidence"]},
            {"CrawlSEO", "SEOmator"},
        )

    def test_partial_source_finding_is_supported_not_verified(self) -> None:
        for source, payload in (("CrawlSEO", _crawlseo()), ("SEOmator", _seomator())):
            with self.subTest(source=source):
                findings = SERVICE.normalize_findings("example-com", {source: payload})
                supported = [item for item in findings if item["rule_key"] == "meta-description-missing"]
                self.assertEqual(len(supported), 1)
                self.assertEqual(supported[0]["evidence_level"], "supported")
                self.assertEqual(len(supported[0]["evidence"]), 1)

    def test_ct_sc_006_keeps_one_verified_fact_and_rejects_invented_metrics(self) -> None:
        finding = SERVICE.normalize_one_verified_finding(
            "example-com", _crawlseo(), _seomator()
        )
        self.assertEqual(finding["rule_key"], "meta-description-missing")
        self.assertEqual(finding["evidence_level"], "verified")
        self.assertEqual(len(finding["evidence"]), 2)
        model_input = SERVICE.build_model_input(finding)

        def invented(_message: str) -> dict[str, str]:
            return {
                "context_id": "test",
                "response": json.dumps(
                    {
                        "business_impact": "Это увеличит CTR на 10 процентов.",
                        "minimal_fix": "Добавить описание.",
                    }
                ),
            }

        with self.assertRaisesRegex(SERVICE.SeoEmployeeError, "unsupported claims"):
            SERVICE.enrich_with_agent_zero(model_input, agent_call=invented)

        for unsupported in (
            "Это значительно увеличит выручку компании.",
            "This causes more client traffic.",
        ):
            with self.subTest(unsupported=unsupported):
                with self.assertRaisesRegex(SERVICE.SeoEmployeeError, "unsupported claims"):
                    SERVICE._validate_enrichment(
                        {"business_impact": unsupported, "minimal_fix": "Добавить описание."}
                    )

    def test_ct_sc_007_stably_sorts_caps_ten_and_excludes_unverified(self) -> None:
        findings = [
            {
                "site_id": "example-com",
                "url": f"https://example.com/{index:02d}",
                "rule_key": "meta-description-missing",
                "severity": "warning" if index else "critical",
                "evidence_level": "verified" if index != 14 else "unverified",
                "affected_pages_count": (index % 3) + 1,
            }
            for index in range(15)
        ]
        first = SERVICE.prioritize_findings(findings)
        second = SERVICE.prioritize_findings(reversed(findings))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 10)
        self.assertTrue(all(item["evidence_level"] != "unverified" for item in first))
        self.assertEqual(first[0]["severity"], "critical")

    def test_ct_sc_008_compares_baseline_and_preserves_it_on_incomplete_runs(self) -> None:
        current = [
            {"task_id": "b", "url": "https://example.com/b", "rule_key": "meta-description-missing"},
            {"task_id": "c", "url": "https://example.com/c", "rule_key": "meta-description-missing"},
        ]
        baseline = {
            "items": [
                {"task_id": "a", "url": "https://example.com/a", "rule_key": "meta-description-missing"},
                {"task_id": "b", "url": "https://example.com/b", "rule_key": "meta-description-missing"},
            ]
        }
        comparison, _next = SERVICE.compare_with_baseline(current, baseline)
        self.assertEqual(
            {key: comparison[key] for key in ("new", "fixed", "unchanged")},
            {"new": 1, "fixed": 1, "unchanged": 1},
        )
        self.assertEqual(comparison["new_items"][0]["task_id"], "c")
        self.assertEqual(comparison["fixed_items"][0]["task_id"], "a")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _service_paths(root)
            _configure(paths)
            SERVICE.atomic_write_json(paths["baseline_path"], baseline)
            before = paths["baseline_path"].read_text(encoding="utf-8")
            calls: list[list[str]] = []
            paths["process_runner"] = _source_runner(
                calls, failures={"CrawlSEO", "SEOmator"}
            )
            result = SERVICE.run_seo_employee(site_url="", **paths)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["report"]["comparison"]["baseline"], "not_compared")
            self.assertEqual(
                {key: result["report"]["comparison"][key] for key in ("new", "fixed", "unchanged")},
                {"new": 0, "fixed": 0, "unchanged": 0},
            )
            self.assertEqual(paths["baseline_path"].read_text(encoding="utf-8"), before)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _service_paths(root)
            _configure(paths)
            ready_calls: list[list[str]] = []
            paths["process_runner"] = _source_runner(ready_calls)
            ready = SERVICE.run_seo_employee(site_url="", **paths)
            self.assertEqual(ready["state"], "ready")
            before = paths["baseline_path"].read_text(encoding="utf-8")

            partial_calls: list[list[str]] = []
            paths["process_runner"] = _source_runner(
                partial_calls,
                crawl={**_crawlseo(), "issues": []},
                failures={"SEOmator"},
            )
            partial = SERVICE.run_seo_employee(site_url="", **paths)
            self.assertEqual(partial["state"], "partial")
            self.assertEqual(partial["report"]["comparison"]["fixed"], 0)
            self.assertEqual(partial["report"]["comparison"]["fixed_items"], [])
            self.assertEqual(paths["baseline_path"].read_text(encoding="utf-8"), before)

            next_calls: list[list[str]] = []
            paths["process_runner"] = _source_runner(next_calls)
            next_ready = SERVICE.run_seo_employee(site_url="", **paths)
            self.assertEqual(next_ready["state"], "ready")
            self.assertEqual(next_ready["report"]["comparison"]["new"], 0)
            self.assertEqual(next_ready["report"]["comparison"]["fixed"], 0)
            self.assertEqual(next_ready["report"]["comparison"]["unchanged"], 2)

    def test_baseline_preserves_full_fixed_cards_and_legacy_minimal_cards(self) -> None:
        fixed = {
            "task_id": "fixed-rich",
            "site_id": "example-com",
            "url": "https://example.com/fixed",
            "rule_key": "meta-description-missing",
            "severity": "warning",
            "evidence_level": "verified",
            "sources": ["CrawlSEO", "SEOmator"],
            "confirmed_fact": "На странице отсутствует meta description.",
            "business_impact": "Сниппет страницы может быть менее понятным в поисковой выдаче.",
            "minimal_fix": "Добавить уникальное meta description по содержанию страницы.",
            "verification": "Повторить проверку и убедиться, что правило больше не срабатывает.",
            "evidence": [{"source": "CrawlSEO", "fact": "Missing meta description"}],
        }
        legacy = {"task_id": "legacy", "url": "https://example.com/legacy", "rule_key": "meta-description-missing"}
        current = [
            {"task_id": f"current-{index:02d}", "url": f"https://example.com/{index}", "rule_key": "meta-description-missing", "confirmed_fact": "fact"}
            for index in range(11)
        ]
        comparison, next_baseline = SERVICE.compare_with_baseline(
            current,
            {"schema": "extella.seo_employee_baseline.v1", "items": [fixed, legacy]},
        )
        self.assertEqual(comparison["fixed_items"], [fixed, legacy])
        self.assertEqual(len(next_baseline["items"]), 10)
        self.assertEqual(next_baseline["items"][0], current[0])
        self.assertEqual(next_baseline["items"][-1], current[9])

    def test_runtime_baseline_is_scoped_to_the_configured_site(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _service_paths(root)
            _configure(paths)
            SERVICE.atomic_write_json(
                paths["baseline_path"],
                {
                    "schema": "extella.seo_employee_baseline.v2",
                    "target_id": "another-target",
                    "items": [{"task_id": "old", "url": "https://another.example/", "rule_key": "meta-description-missing"}],
                },
            )
            calls: list[list[str]] = []
            paths["process_runner"] = _source_runner(calls)
            paths["enricher"] = _enrich
            result = SERVICE.run_seo_employee(site_url="", **paths)
            self.assertEqual(result["report"]["comparison"]["baseline"], "not_compared")
            self.assertEqual(result["report"]["comparison"]["fixed"], 0)
            saved = json.loads(paths["baseline_path"].read_text(encoding="utf-8"))
            self.assertEqual(saved["target_id"], "another-target")

    def test_ct_sc_009_all_states_and_empty_reader_satisfy_state_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = json.loads(
                STATE.seo_employee_state(
                    state_path=root / "missing-state.json",
                    config_path=root / "missing-config.json",
                    now_provider=_fixed_now,
                )
            )
            self.assertEqual(empty["state"], "empty")
            self.assertTrue(
                {
                    "enabled",
                    "active_version",
                    "last_run",
                    "last_result",
                    "last_error",
                    "schedules",
                    "checked_at",
                    "bound_to",
                    "config",
                    "state",
                    "last_report",
                }.issubset(empty)
            )

        for state in ("empty", "running", "ready", "partial", "failed"):
            error = SERVICE._safe_failure("SEO_RUN_FAILED") if state == "failed" else None
            value = SERVICE.make_state(
                state,
                checked_at="2026-08-29T18:00:00Z",
                config=None,
                last_error=error,
            )
            self.assertIsInstance(value["enabled"], bool, state)
            self.assertIsInstance(value["schedules"], list, state)
            self.assertEqual(
                set(value["bound_to"]),
                {"hosting_profile", "host", "platform_profile_id", "account_ref", "agent_ids", "since", "device_id"},
                state,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _service_paths(root)
            _configure(paths)
            observed: list[str] = []

            def runner(args: list[str], **_kwargs: object) -> SimpleNamespace:
                observed.append(json.loads(paths["state_path"].read_text(encoding="utf-8"))["state"])
                payload = _crawlseo() if args[0].endswith("run_crawlseo") else _seomator()
                Path(args[3]).write_text(json.dumps(payload), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            paths["process_runner"] = runner
            result = SERVICE.run_seo_employee(site_url="", **paths)
            self.assertEqual(observed, ["running", "running"])
            self.assertEqual(
                json.loads(paths["state_path"].read_text(encoding="utf-8"))["state"],
                "ready",
            )
            self.assertEqual(result["state"], "ready")

    def test_state_exposes_exact_next_run_and_validated_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _service_paths(root)
            _configure(paths)
            device = root / "device_binding.json"
            agent = root / "agent_binding.json"
            SERVICE.atomic_write_json(
                device,
                {
                    "device_id": "device-seo-01",
                    "host": "seo-employee.local",
                    "hosting_profile": "client_server",
                    "since": "2026-08-29T17:00:00Z",
                },
            )
            SERVICE.atomic_write_json(agent, {"agent_id": "agent_seo_01"})
            state = SERVICE.make_state(
                "ready",
                checked_at="2026-08-29T18:00:00Z",
                config=SERVICE.load_configuration(paths["config_path"]),
                device_binding_path=device,
                agent_binding_path=agent,
            )
            self.assertEqual(state["schedules"][0]["next_run"], "2026-08-30T21:00:00+05:00")
            self.assertEqual(
                state["bound_to"],
                {
                    "hosting_profile": "client_server",
                    "host": "seo-employee.local",
                    "platform_profile_id": None,
                    "account_ref": None,
                    "agent_ids": ["agent_seo_01"],
                    "since": "2026-08-29T17:00:00Z",
                    "device_id": "device-seo-01",
                },
            )
            SERVICE.atomic_write_json(agent, {"agent_id": "not-a-platform-agent"})
            public = SERVICE.make_state("ready", checked_at="2026-08-29T18:00:00Z", config=SERVICE.load_configuration(paths["config_path"]), device_binding_path=device, agent_binding_path=agent)
            self.assertEqual(public["bound_to"]["device_id"], None)
            self.assertIsNone(public["bound_to"]["agent_ids"])

    def test_timeout_path_finishes_without_pending_source_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _service_paths(root)
            _configure(paths)
            calls: list[str] = []

            def timeout_runner(args: list[str], **_kwargs: object) -> SimpleNamespace:
                calls.append(args[0])
                raise subprocess.TimeoutExpired(args, 0.01)

            paths["process_runner"] = timeout_runner
            result = SERVICE.run_seo_employee(site_url="", **paths)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(len(calls), 2)
            self.assertEqual(list(paths["lock_dir"].glob("*.lock")), [])

    def test_http_semantics_keep_partial_successful_and_duplicate_accepted(self) -> None:
        with mock.patch.object(
            SERVER,
            "seo_employee_run",
            return_value=json.dumps({"status": "partial", "state": "partial"}),
        ):
            status, payload = SERVER.dispatch("POST", "/api/run", {"target_id": "target-example-com-0f115db0"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "partial")
        with mock.patch.object(
            SERVER,
            "seo_employee_run",
            return_value=json.dumps({"status": "success", "state": "duplicate", "duplicate": True}),
        ):
            status, payload = SERVER.dispatch("POST", "/api/run", {"target_id": "target-example-com-0f115db0"})
        self.assertEqual(status, 202)
        self.assertTrue(payload["duplicate"])

    def test_ct_sc_010_ready_partial_failed_and_model_partial_are_terminal(self) -> None:
        cases = (
            (set(), _enrich, "ready", []),
            ({"SEOmator"}, _enrich, "partial", ["SEOmator"]),
            ({"CrawlSEO", "SEOmator"}, _enrich, "failed", ["CrawlSEO", "SEOmator"]),
            (set(), lambda _value: (_ for _ in ()).throw(RuntimeError("route down")), "ready", []),
        )
        for failures, enricher, expected, missing in cases:
            with self.subTest(expected=expected, missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paths = _service_paths(root)
                _configure(paths)
                calls: list[list[str]] = []
                paths["process_runner"] = _source_runner(calls, failures=failures)
                paths["enricher"] = enricher
                result = SERVICE.run_seo_employee(site_url="", **paths)
                self.assertEqual(result["state"], expected)
                self.assertEqual(len(calls), 2)
                report = result["report"]
                self.assertEqual(report["missing_data"], missing)
                if expected == "failed":
                    self.assertEqual(report["error"]["code"], "SEO_SOURCES_UNAVAILABLE")
                    self.assertNotIn("route down", json.dumps(result))
                if enricher is not _enrich and expected == "ready":
                    self.assertNotIn("business_impact", report["tasks"][0])
                    self.assertNotIn("minimal_fix", report["tasks"][0])
                    self.assertEqual(report["model_enrichment"]["status"], "unavailable")

    def test_ct_sc_011_writes_atomically_and_refuses_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            SERVICE.atomic_write_json(path, {"schema": "safe", "value": "old"})
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(SERVICE.SeoEmployeeError, "unsafe field"):
                SERVICE.atomic_write_json(path, {"schema": "unsafe", "api_key": "hidden"})
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

            unsafe_state = Path(directory) / "unsafe-state.json"
            unsafe_state.write_text(
                json.dumps({"schema": SERVICE.STATE_SCHEMA, "state": "ready", "api_key": "not-exposed"}),
                encoding="utf-8",
            )
            public_state = json.loads(
                STATE.seo_employee_state(
                    state_path=unsafe_state,
                    config_path=Path(directory) / "missing-config.json",
                    now_provider=_fixed_now,
                )
            )
            self.assertNotIn("api_key", public_state)
            self.assertEqual(public_state["last_error"]["code"], "SEO_STATE_INVALID")

    def test_ct_sc_012_has_no_external_write_and_state_reads_report(self) -> None:
        calls: list[dict[str, object]] = []

        def enrich(value: dict[str, object]) -> dict[str, str]:
            calls.append(value)
            return {
                "business_impact": "Сниппет страницы может быть менее понятным в поисковой выдаче.",
                "minimal_fix": "Добавить уникальное meta description по содержанию страницы.",
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            crawl = root / "crawl.json"
            seo = root / "seo.json"
            report = root / "report.json"
            state = root / "state.json"
            crawl.write_text(json.dumps(_crawlseo()), encoding="utf-8")
            seo.write_text(json.dumps(_seomator()), encoding="utf-8")
            with mock.patch("socket.create_connection", side_effect=AssertionError("network write")):
                value = SERVICE.complete_from_evidence(
                    _command(),
                    crawlseo_path=crawl,
                    seomator_path=seo,
                    report_path=report,
                    state_path=state,
                    resolver=_public_resolver,
                    enricher=enrich,
                )
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(value["tasks"]), 2)
            state_value = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(state_value["schema"], "extella.seo_employee_state.v2")
            self.assertEqual(state_value["last_report"], value)

    def test_ct_sc_013_enforces_allowlist_and_sanitizes_model_url(self) -> None:
        finding = SERVICE.normalize_one_verified_finding(
            "example-com", _crawlseo(), _seomator()
        )
        finding["url"] = "https://user:pass@example.com/path?q=private#fragment"
        value = SERVICE.build_model_input(finding)
        self.assertEqual(value["url"], "https://example.com/path")
        self.assertEqual(set(value), SERVICE.MODEL_FIELDS)

        invoked = False

        def must_not_run(_message: str) -> dict[str, str]:
            nonlocal invoked
            invoked = True
            return {"context_id": "bad", "response": "{}"}

        invalid = {**value, "raw_html": "<html>private</html>"}
        with self.assertRaisesRegex(SERVICE.ModelInputError, "SC-SEO-031"):
            SERVICE.enrich_with_agent_zero(invalid, agent_call=must_not_run)
        self.assertFalse(invoked)

        with tempfile.TemporaryDirectory() as directory:
            config = SERVICE.save_configuration(
                "https://example.com/path?access=private#fragment",
                "00:00",
                "UTC",
                config_path=Path(directory) / "config.json",
                resolver=_public_resolver,
            )
            self.assertEqual(config["targets"][0]["site_url"], "https://example.com/path")

    def test_ct_sc_014_is_provider_neutral_and_preflight_is_fixed(self) -> None:
        source = (ROOT / "experts" / "seo_employee_service.py").read_text(encoding="utf-8").lower()
        for provider_specific in ("gemini", "anthropic", "openai", "alibaba", "qwen"):
            self.assertNotIn(provider_specific, source)

        response = json.dumps(
            {
                "business_impact": "Проверка маршрута завершена.",
                "minimal_fix": "Дополнительных действий не требуется.",
            },
            ensure_ascii=False,
        )
        with mock.patch.object(
            RUN,
            "_call_agent_zero",
            return_value={"context_id": "ctx", "response": response},
        ) as call:
            result = json.loads(RUN.seo_employee_run(method="preflight"))
        self.assertEqual(result, {"status": "success", "method": "preflight", "result": "ok"})
        self.assertEqual(call.call_args.args, (RUN._PREFLIGHT_PROMPT,))
        self.assertNotIn("site_url", RUN._PREFLIGHT_PROMPT)

    def test_configure_returns_the_new_empty_state_envelope(self) -> None:
        target = {
            "target_id": "target-example-com-0f115db0", "target_name": "example.com",
            "site_url": "https://example.com/", "profile": "service_b2b", "language": "ru",
            "region": "GLOBAL", "site_type": "website", "business_goal": "organic_visibility",
            "daily_run_time": "21:00", "timezone": "Asia/Tashkent", "max_pages": 25,
            "ownership_confirmed": True, "mode": "daily_monitor",
        }
        config = {"schema": SERVICE.CONFIG_SCHEMA, "targets": [target]}
        with (
            mock.patch.object(SERVICE, "save_configuration", return_value=config),
        ):
            result = json.loads(
                RUN.seo_employee_run(
                    method="configure",
                    site_url=target["site_url"],
                    daily_run_time=target["daily_run_time"],
                    timezone=target["timezone"],
                    ownership_confirmed=True,
                )
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["method"], "configure")
        self.assertEqual(result["target_id"], target["target_id"])
        self.assertEqual(result["config"], target)


if __name__ == "__main__":
    unittest.main()
