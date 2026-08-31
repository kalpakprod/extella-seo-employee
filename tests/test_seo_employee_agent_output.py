from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from experts import seo_employee_run as run
from experts import seo_employee_schedule as schedule
from experts import seo_employee_service as service
from experts import seo_employee_state as state
from experts.seo_employee_queue import SeoEmployeeQueue
from experts.seo_employee_targets import migrate_config


class SeoEmployeeAgentOutputTest(unittest.TestCase):
    def test_run_rejects_missing_ownership_before_queue_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = migrate_config({
                "schema": "extella.seo_employee_config.v1", "site_id": "example.com",
                "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC",
            })
            config_path.write_text(json.dumps(config), encoding="utf-8")
            queue_path = root / "state" / "queue.json"

            payload = json.loads(run.seo_employee_run(
                method="run", target_id=config["targets"][0]["target_id"],
                config_path=config_path, queue_path=queue_path,
            ))

            self.assertEqual(payload["error"]["code"], "ownership_confirmation_required")
            self.assertFalse(queue_path.exists())

    def test_run_enqueues_and_deduplicates_same_target_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = migrate_config({
                "schema": "extella.seo_employee_config.v1", "site_id": "example.com",
                "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC",
            })
            config["targets"][0]["ownership_confirmed"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")
            target_id = config["targets"][0]["target_id"]
            queue_path = root / "state" / "queue.json"

            first = json.loads(run.seo_employee_run(method="run", target_id=target_id, config_path=config_path, queue_path=queue_path))
            duplicate = json.loads(run.seo_employee_run(method="run", target_id=target_id, config_path=config_path, queue_path=queue_path))

            self.assertEqual(first["state"], "queued")
            self.assertFalse(first["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["queue_item"]["queue_id"], first["queue_item"]["queue_id"])
            self.assertEqual(len(SeoEmployeeQueue(queue_path).snapshot()), 1)

    def test_process_queue_once_completes_exact_claimed_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = SeoEmployeeQueue(root / "state" / "queue.json")
            item = queue.enqueue("run-one", "target-example-com-0f115db0", "manual", "daily_monitor", "2026-08-30T12:00:00Z")

            result = run.process_queue_once(
                queue=queue,
                worker=lambda **kwargs: {"state": "ready", "run_id": kwargs["target_id"]},
            )

            self.assertEqual(result["queue_item"]["queue_id"], item.queue_id)
            self.assertEqual(SeoEmployeeQueue(queue.path).get(item.queue_id).status, "completed")

    def test_consumer_recovers_once_and_waits_without_busy_loop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = SeoEmployeeQueue(root / "state" / "queue.json")
            queued = queue.enqueue("run-one", "target-example-com-0f115db0", "manual", "daily_monitor", "2026-08-30T12:00:00Z")
            queue.claim_next()
            consumer = run.QueueConsumer(queue=queue, worker=lambda **_kwargs: {"state": "ready"}, wake_seconds=0.01)

            consumer.start()
            consumer.stop(join_timeout=1)

            self.assertEqual(consumer.recovery_count, 1)
            self.assertEqual(SeoEmployeeQueue(queue.path).get(queued.queue_id).status, "completed")

    def test_preflight_is_fixed_and_does_not_accept_prompt(self) -> None:
        self.assertNotIn("site_url", run._PREFLIGHT_PROMPT)
        payload = json.loads(run.seo_employee_run(method="not-a-prompt"))
        self.assertEqual(payload["error"]["code"], "SEO_METHOD_UNSUPPORTED")

    def test_state_exposes_fifo_queue_items_and_selected_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = migrate_config({
                "schema": "extella.seo_employee_config.v1", "site_id": "example.com",
                "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC",
            })
            first = config["targets"][0]
            second = migrate_config({
                "schema": "extella.seo_employee_config.v1", "site_id": "example.org",
                "site_url": "https://example.org/", "daily_run_time": "12:00", "timezone": "UTC",
            })["targets"][0]
            config["targets"].append(second)
            config_path.write_text(json.dumps(config), encoding="utf-8")
            queue = SeoEmployeeQueue(root / "state" / "queue.json")
            queue.enqueue("run-one", first["target_id"], "manual", "daily_monitor", "2026-08-30T12:00:00Z")
            queue.enqueue("run-two", second["target_id"], "manual", "daily_monitor", "2026-08-30T12:01:00Z")

            value = json.loads(state.seo_employee_state(target_id=second["target_id"], config_path=config_path))

            self.assertEqual(value["state"], "empty")
            self.assertEqual(value["queue"]["position"], 2)
            self.assertEqual([item["target_id"] for item in value["queue"]["items"]], [first["target_id"], second["target_id"]])

    def test_scheduler_enqueues_each_due_target_and_keeps_going_after_one_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "config.json"
            config_path.parent.mkdir(parents=True)
            first = migrate_config({
                "schema": "extella.seo_employee_config.v1", "site_id": "example.com",
                "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC",
            })
            second = migrate_config({
                "schema": "extella.seo_employee_config.v1", "site_id": "example.org",
                "site_url": "https://example.org/", "daily_run_time": "12:00", "timezone": "UTC",
            })["targets"][0]
            first["targets"].append(second)
            config_path.write_text(json.dumps(first), encoding="utf-8")
            calls: list[str] = []

            def enqueue(**kwargs: object) -> str:
                target_id = str(kwargs["target_id"])
                calls.append(target_id)
                if target_id == first["targets"][0]["target_id"]:
                    raise ValueError("invalid target")
                return json.dumps({"state": "queued"})

            result = json.loads(schedule.seo_employee_schedule(
                config_path=config_path,
                now_provider=lambda: datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc),
                run=enqueue,
            ))

            self.assertEqual(calls, [item["target_id"] for item in first["targets"]])
            self.assertEqual(result["targets"][0]["state"], "failed")
            self.assertEqual(result["targets"][1]["state"], "queued")

    def test_unknown_target_and_invalid_mode_are_input_errors_not_queue_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.com", "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC"})
            config["targets"][0]["ownership_confirmed"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")
            unknown = json.loads(run.seo_employee_run(method="run", target_id="target-other-01234567", config_path=config_path, queue_path=root / "queue.json"))
            invalid_mode = json.loads(run.seo_employee_run(method="run", target_id=config["targets"][0]["target_id"], mode="invalid", config_path=config_path, queue_path=root / "queue.json"))
        self.assertEqual(unknown["error"]["code"], "SEO_RUN_INPUT_INVALID")
        self.assertEqual(invalid_mode["error"]["code"], "SEO_RUN_INPUT_INVALID")

    def test_duplicate_flag_uses_returned_queue_item_under_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.com", "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC"})
            config["targets"][0]["ownership_confirmed"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")
            target_id = config["targets"][0]["target_id"]
            barrier = threading.Barrier(2)
            results: list[dict[str, object]] = []
            def enqueue() -> None:
                barrier.wait()
                results.append(json.loads(run.seo_employee_run(method="run", target_id=target_id, config_path=config_path, queue_path=root / "queue.json")))
            threads = [threading.Thread(target=enqueue) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        self.assertEqual(sorted(item["duplicate"] for item in results), [False, True])

    def test_queue_position_ignores_terminal_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = SeoEmployeeQueue(Path(directory) / "queue.json")
            completed = queue.enqueue("run-one", "target-first-01234567", "manual", "daily_monitor", "2026-08-30T12:00:00Z")
            queue.claim_next(); queue.complete(completed.queue_id)
            queued = queue.enqueue("run-two", "target-second-01234567", "manual", "daily_monitor", "2026-08-30T12:01:00Z")
            summary = state._queue_state(queue.path, queued.target_id)
        self.assertEqual(summary["position"], 1)

    def test_consumer_stop_raises_when_worker_does_not_terminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = SeoEmployeeQueue(Path(directory) / "queue.json")
            entered = threading.Event()
            release = threading.Event()
            def blocked_worker(**_kwargs: object) -> dict[str, object]:
                entered.set(); release.wait(1); return {"state": "ready"}
            queue.enqueue("run-one", "target-example-com-0f115db0", "manual", "daily_monitor", "2026-08-30T12:00:00Z")
            consumer = run.QueueConsumer(queue=queue, worker=blocked_worker, wake_seconds=0.01)
            consumer.start(); self.assertTrue(entered.wait(0.5))
            with self.assertRaisesRegex(RuntimeError, "SEO_QUEUE_SHUTDOWN_FAILED"):
                consumer.stop(join_timeout=0.01)
            release.set(); consumer.stop(join_timeout=1)

    def test_state_distinguishes_empty_unknown_and_corrupt_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = json.loads(state.seo_employee_state(config_path=root / "missing" / "config.json"))
            self.assertEqual(missing["state"], "empty")
            config_path = root / "config" / "config.json"; config_path.parent.mkdir(parents=True)
            config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.com", "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC"})
            config_path.write_text(json.dumps(config), encoding="utf-8")
            unknown = json.loads(state.seo_employee_state(target_id="target-other-01234567", config_path=config_path))
            self.assertEqual(unknown["error"]["code"], "SEO_TARGET_NOT_FOUND")
            config_path.write_text("{not-json", encoding="utf-8")
            corrupt = json.loads(state.seo_employee_state(config_path=config_path))
            self.assertEqual((corrupt["state"], corrupt["last_error"]["code"]), ("failed", "SEO_CONFIGURATION_INVALID"))

    def test_targetless_multi_target_state_includes_safe_bootstrap_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config_path = root / "config" / "config.json"; config_path.parent.mkdir(parents=True)
            config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.com", "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC"})
            second = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.org", "site_url": "https://example.org/", "daily_run_time": "12:00", "timezone": "UTC"})["targets"][0]
            config["targets"].append(second); config_path.write_text(json.dumps(config), encoding="utf-8")
            value = json.loads(state.seo_employee_state(config_path=config_path))
        self.assertEqual(value["state"], "empty")
        self.assertEqual(set(value["targets"][0]), {"target_id", "target_name", "profile", "site_url", "state", "queue_position"})

    def test_configure_accepts_service_normalized_equivalent_url_for_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config_path = root / "config" / "config.json"; config_path.parent.mkdir(parents=True)
            config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.com", "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC"})
            target = config["targets"][0]; config_path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch.object(service, "validate_public_url", return_value=target["site_url"]):
                result = json.loads(run.seo_employee_run(method="configure", target_id=target["target_id"], site_url="https://EXAMPLE.com", daily_run_time="13:00", timezone="UTC", ownership_confirmed=True, config_path=config_path))
        self.assertEqual(result["target_id"], target["target_id"])

    def test_configure_rejects_different_existing_target_url_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config_path = root / "config" / "config.json"; config_path.parent.mkdir(parents=True)
            config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.com", "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC"})
            target = config["targets"][0]; config_path.write_text(json.dumps(config), encoding="utf-8")
            original = config_path.read_bytes()
            result = json.loads(run.seo_employee_run(method="configure", target_id=target["target_id"], site_url="https://example.org/", daily_run_time="12:00", timezone="UTC", ownership_confirmed=True, config_path=config_path))
            self.assertEqual(config_path.read_bytes(), original)
        self.assertEqual(result["error"]["code"], "SEO_CONFIGURATION_INVALID")

    def test_configure_existing_unreadable_file_is_storage_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"; path.write_text("{}", encoding="utf-8")
            with mock.patch.object(Path, "read_text", side_effect=OSError("private path")):
                result = json.loads(run.seo_employee_run(method="configure", site_url="https://example.com/", daily_run_time="12:00", timezone="UTC", ownership_confirmed=True, config_path=path))
        self.assertEqual(result["error"]["code"], "SEO_CONFIGURATION_UNAVAILABLE")

    def test_target_list_reports_corrupt_configuration_as_safe_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "config.json"; path.parent.mkdir(parents=True); path.write_text("{bad", encoding="utf-8")
            value = state.list_target_states(config_path=path)
        self.assertEqual(value["error"]["code"], "SEO_CONFIGURATION_INVALID")

    def test_scheduler_skips_non_object_and_runtime_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config_path = root / "config" / "config.json"; config_path.parent.mkdir(parents=True)
            config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.com", "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC"})
            config["targets"].append(migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.org", "site_url": "https://example.org/", "daily_run_time": "12:00", "timezone": "UTC"})["targets"][0])
            config_path.write_text(json.dumps(config), encoding="utf-8"); calls = 0
            def malformed(**_kwargs: object) -> str:
                nonlocal calls; calls += 1
                return "[]" if calls == 1 else json.dumps({"state": "queued"})
            value = json.loads(schedule.seo_employee_schedule(config_path=config_path, now_provider=lambda: datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc), run=malformed))
        self.assertEqual([item["state"] for item in value["targets"]], ["failed", "queued"])

    def test_scheduler_does_not_enqueue_a_completed_local_day_again_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config_path = root / "config" / "config.json"; config_path.parent.mkdir(parents=True)
            config = migrate_config({"schema": "extella.seo_employee_config.v1", "site_id": "example.com", "site_url": "https://example.com/", "daily_run_time": "12:00", "timezone": "UTC"})
            target = config["targets"][0]; config_path.write_text(json.dumps(config), encoding="utf-8")
            now = datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc)
            daily_path = service.target_paths(root, target["target_id"])["daily_index"]
            service._record_completed_daily_run(
                daily_path,
                service._daily_key(target["target_id"], now, "UTC"),
                "run-complete",
                "ready",
            )
            run_once = mock.Mock(side_effect=AssertionError("completed day must not enqueue"))
            first = json.loads(schedule.seo_employee_schedule(config_path=config_path, now_provider=lambda: now, run=run_once))
            second = json.loads(schedule.seo_employee_schedule(config_path=config_path, now_provider=lambda: now, run=run_once))
        self.assertEqual(first["targets"][0]["state"], "duplicate")
        self.assertEqual(second["targets"][0]["state"], "duplicate")
        run_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()
