from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import experts.seo_employee_queue as QUEUE_MODULE
from experts.seo_employee_queue import (
    QUEUE_SCHEMA,
    QueueOperationError,
    QueueValidationError,
    SeoEmployeeQueue,
)


REQUESTED_AT = "2026-08-30T12:00:00Z"


def _enqueue(queue: SeoEmployeeQueue, number: int, *, target: str = "target-alpha", trigger: str = "manual"):
    return queue.enqueue(
        run_id=f"run-{number}",
        target_id=target,
        trigger=trigger,
        mode="full_audit",
        requested_at=REQUESTED_AT,
    )


class SeoEmployeeQueueTest(unittest.TestCase):
    def test_empty_queue_handle_validates_without_writing(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "queue.json"
            queue = SeoEmployeeQueue(path)

            self.assertEqual(queue.snapshot(), ())
            self.assertFalse(path.exists())

    def test_fifo_order_is_preserved_across_targets(self) -> None:
        with TemporaryDirectory() as directory:
            queue = SeoEmployeeQueue(Path(directory) / "queue.json")
            first = _enqueue(queue, 1, target="target-alpha")
            second = _enqueue(queue, 2, target="target-beta")

            claimed_first = queue.claim_next()
            self.assertEqual(claimed_first.queue_id, first.queue_id)
            self.assertEqual(claimed_first.status, "running")
            self.assertEqual(claimed_first.target_id, "target-alpha")
            queue.complete(claimed_first.queue_id)
            claimed_second = queue.claim_next()
            self.assertIsNotNone(claimed_second)
            self.assertEqual(claimed_second.queue_id, second.queue_id)
            self.assertEqual(claimed_second.status, "running")

    def test_same_target_and_trigger_deduplicate_only_while_active(self) -> None:
        with TemporaryDirectory() as directory:
            queue = SeoEmployeeQueue(Path(directory) / "queue.json")
            first = _enqueue(queue, 1)
            duplicate = _enqueue(queue, 2)
            self.assertEqual(duplicate, first)
            self.assertEqual(len(queue.snapshot()), 1)

            queue.claim_next()
            running_duplicate = _enqueue(queue, 3)
            self.assertEqual(running_duplicate.queue_id, first.queue_id)
            queue.complete(first.queue_id)
            after_completion = _enqueue(queue, 4)
            self.assertNotEqual(after_completion.queue_id, first.queue_id)

    def test_persisted_readback_has_only_public_queue_fields_and_no_temp_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state" / "queue.json"
            queue = SeoEmployeeQueue(path)
            item = _enqueue(queue, 1)

            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema"], QUEUE_SCHEMA)
            self.assertEqual(document["items"][0], {
                "attempts": 0,
                "mode": "full_audit",
                "queue_id": item.queue_id,
                "requested_at": REQUESTED_AT,
                "run_id": "run-1",
                "status": "queued",
                "target_id": "target-alpha",
                "trigger": "manual",
            })
            self.assertEqual(list(root.rglob("*.tmp")), [])
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_atomic_write_uses_same_directory_temp_flush_fsync_and_replace(self) -> None:
        class SpyTemporaryFile:
            def __init__(self, factory, *args, **kwargs):
                self._context = factory(*args, **kwargs)
                self.kwargs = kwargs
                self.path: Path | None = None
                self.flush = mock.Mock()
                self._handle = None

            def __enter__(self):
                self._handle = self._context.__enter__()
                self.path = Path(self._handle.name)
                self.flush = mock.Mock(wraps=self._handle.flush)
                return self

            def __exit__(self, *args):
                return self._context.__exit__(*args)

            def write(self, value):
                return self._handle.write(value)

            def fileno(self):
                return self._handle.fileno()

            @property
            def name(self):
                return self._handle.name

        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state" / "queue.json"
            spies: list[SpyTemporaryFile] = []
            real_factory = QUEUE_MODULE.tempfile.NamedTemporaryFile

            def factory(*args, **kwargs):
                spy = SpyTemporaryFile(real_factory, *args, **kwargs)
                spies.append(spy)
                return spy

            with mock.patch.object(QUEUE_MODULE.tempfile, "NamedTemporaryFile", side_effect=factory), \
                mock.patch.object(QUEUE_MODULE.os, "fsync", wraps=QUEUE_MODULE.os.fsync) as fsync, \
                mock.patch.object(QUEUE_MODULE.os, "replace", wraps=QUEUE_MODULE.os.replace) as replace:
                queue = SeoEmployeeQueue(path)
                item = _enqueue(queue, 1)

            self.assertEqual(len(spies), 1)
            self.assertEqual(spies[0].kwargs["dir"], path.parent)
            self.assertIsNotNone(spies[0].path)
            self.assertEqual(spies[0].path.parent, path.parent)
            spies[0].flush.assert_called_once_with()
            fsync.assert_called_once()
            replace.assert_called_once()
            temporary, destination = replace.call_args.args
            self.assertEqual(Path(temporary).parent, path.parent)
            self.assertEqual(Path(destination), path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["items"][0]["queue_id"], item.queue_id)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_malformed_json_is_rejected_before_any_write(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            path.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(QueueValidationError):
                SeoEmployeeQueue(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{not-json")

    def test_interrupted_running_item_is_requeued_once_and_attempts_increment(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            first = SeoEmployeeQueue(path)
            item = _enqueue(first, 1)
            first.claim_next()

            recovered = SeoEmployeeQueue(path)
            self.assertEqual(recovered.snapshot()[0].status, "running")
            recovered.recover_interrupted()
            recovered_item = recovered.snapshot()[0]
            self.assertEqual(recovered_item.queue_id, item.queue_id)
            self.assertEqual(recovered_item.status, "queued")
            self.assertEqual(recovered_item.attempts, 1)

            recovered_again = SeoEmployeeQueue(path)
            self.assertEqual(recovered_again.snapshot()[0], recovered_item)
            recovered_again.recover_interrupted()
            self.assertEqual(recovered_again.snapshot()[0], recovered_item)

    def test_second_interrupted_recovery_fails_with_safe_reason(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            queue = SeoEmployeeQueue(path)
            item = _enqueue(queue, 1)
            queue.claim_next()
            recovered = SeoEmployeeQueue(path)
            self.assertEqual(recovered.snapshot()[0].status, "running")
            recovered.recover_interrupted()
            recovered.claim_next()

            recovered.recover_interrupted()
            failed = recovered.snapshot()[0]
            self.assertEqual(failed.queue_id, item.queue_id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.attempts, 2)
            self.assertEqual(failed.failure_reason, "recovery_limit")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["items"][0]["failure_reason"], "recovery_limit")

            stable = SeoEmployeeQueue(path).snapshot()[0]
            self.assertEqual(stable, failed)

    def test_two_live_handles_preserve_running_item_and_never_duplicate_claim(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            first_handle = SeoEmployeeQueue(path)
            second_handle = SeoEmployeeQueue(path)
            first = _enqueue(first_handle, 1, target="target-alpha")
            second = _enqueue(first_handle, 2, target="target-beta")

            claimed = first_handle.claim_next()
            self.assertEqual(claimed.queue_id, first.queue_id)
            self.assertIsNone(second_handle.claim_next())
            self.assertEqual(second_handle.snapshot()[0].status, "running")
            self.assertEqual(second_handle.snapshot()[1].queue_id, second.queue_id)

    def test_claim_allows_only_one_running_item(self) -> None:
        with TemporaryDirectory() as directory:
            queue = SeoEmployeeQueue(Path(directory) / "queue.json")
            first = _enqueue(queue, 1, target="target-alpha")
            second = _enqueue(queue, 2, target="target-beta")

            claimed = queue.claim_next()
            self.assertEqual(claimed.queue_id, first.queue_id)
            self.assertIsNone(queue.claim_next())
            self.assertEqual([item.status for item in queue.snapshot()], ["running", "queued"])
            self.assertEqual(queue.snapshot()[1].queue_id, second.queue_id)

    def test_completion_requires_matching_running_queue_id(self) -> None:
        with TemporaryDirectory() as directory:
            queue = SeoEmployeeQueue(Path(directory) / "queue.json")
            item = _enqueue(queue, 1)
            queue.claim_next()

            with self.assertRaises(QueueOperationError):
                queue.complete("queue-does-not-exist")
            self.assertEqual(queue.snapshot()[0].status, "running")
            completed = queue.complete(item.queue_id)
            self.assertEqual(completed.status, "completed")
            with self.assertRaises(QueueOperationError):
                queue.complete(item.queue_id)

    def test_failed_operation_preserves_other_items(self) -> None:
        with TemporaryDirectory() as directory:
            queue = SeoEmployeeQueue(Path(directory) / "queue.json")
            first = _enqueue(queue, 1, target="target-alpha")
            second = _enqueue(queue, 2, target="target-beta")
            queue.claim_next()

            with self.assertRaises(QueueOperationError):
                queue.fail("queue-does-not-exist", reason="worker_failed")
            self.assertEqual(queue.snapshot()[0].status, "running")
            self.assertEqual(queue.snapshot()[1].queue_id, second.queue_id)
            failed = queue.fail(first.queue_id, reason="worker_failed")
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.failure_reason, "worker_failed")
            self.assertEqual(queue.snapshot()[1].status, "queued")

    def test_concurrent_enqueue_is_serialized_by_process_local_mutex(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            worker_count = 16
            handles = [SeoEmployeeQueue(path) for _ in range(worker_count)]
            barrier = threading.Barrier(worker_count)
            outputs: list[str] = []
            failures: list[BaseException] = []

            def worker(number: int) -> None:
                try:
                    barrier.wait(timeout=5)
                    outputs.append(_enqueue(handles[number], number, target=f"target-{number}").queue_id)
                except BaseException as error:  # pragma: no cover - surfaced below
                    failures.append(error)

            threads = [threading.Thread(target=worker, args=(number,)) for number in range(worker_count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertEqual(failures, [])
            self.assertEqual(len(outputs), worker_count)
            self.assertEqual(len(set(outputs)), worker_count)
            self.assertEqual(len(handles[0].snapshot()), worker_count)
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8")).get("items", [])), worker_count)

    def test_unknown_fields_duplicate_ids_secret_fields_and_invalid_values_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            valid_item = {
                "queue_id": "queue-1",
                "run_id": "run-1",
                "target_id": "target-alpha",
                "trigger": "manual",
                "mode": "full_audit",
                "requested_at": REQUESTED_AT,
                "status": "queued",
                "attempts": 0,
            }
            invalid_documents = [
                {"schema": QUEUE_SCHEMA, "items": [{**valid_item, "extra": True}]},
                {"schema": QUEUE_SCHEMA, "items": [valid_item, dict(valid_item)]},
                {"schema": QUEUE_SCHEMA, "items": [{**valid_item, "status": "unknown"}]},
                {"schema": QUEUE_SCHEMA, "items": [{**valid_item, "run_id": "../secret"}]},
                {"schema": QUEUE_SCHEMA, "items": [{**valid_item, "attempts": -1}]},
                {"schema": QUEUE_SCHEMA, "items": [{**valid_item, "requested_at": "2026-08-30T12:00:00"}]},
                {"schema": QUEUE_SCHEMA, "items": [{**valid_item, "failure_reason": "bad reason"}]},
                {"schema": QUEUE_SCHEMA, "items": [{**valid_item, "api_key": "sk-test-secret"}]},
            ]
            for document in invalid_documents:
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(document=document):
                    with self.assertRaises(QueueValidationError):
                        SeoEmployeeQueue(path)

    def test_path_traversal_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaises(QueueValidationError):
                SeoEmployeeQueue(Path(directory) / ".." / "outside" / "queue.json")

    def test_terminal_history_is_bounded_when_new_work_is_enqueued(self) -> None:
        with TemporaryDirectory() as directory, mock.patch.object(QUEUE_MODULE, "MAX_TERMINAL_ITEMS", 2):
            queue = SeoEmployeeQueue(Path(directory) / "queue.json")
            for number in range(1, 4):
                item = _enqueue(queue, number, target=f"target-{number}")
                queue.claim_next()
                queue.complete(item.queue_id)
            items = queue.snapshot()
        self.assertEqual([item.run_id for item in items], ["run-2", "run-3"])


if __name__ == "__main__":
    unittest.main()
