"""Standalone, atomic, file-backed FIFO queue for SEO Employee v2.

The queue deliberately has no worker loop.  A service owns claiming and
processing items; this module only persists the single-worker state safely.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Mapping


QUEUE_SCHEMA: Final = "extella.seo_employee_queue.v1"
QUEUE_TRIGGERS: Final = frozenset({"manual", "daily"})
QUEUE_MODES: Final = frozenset({"full_audit", "daily_monitor", "search_performance", "work_plan"})
QUEUE_STATUSES: Final = frozenset({"queued", "running", "completed", "failed"})
MAX_TERMINAL_ITEMS: Final = 500

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:secret|token|cookie|authorization|api[_-]?key|password|oauth|credential|private[_-]?key)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|sk-[a-z0-9_-]{8,}|"
    r"(?:api[_-]?key|token|secret|password|oauth|cookie)\s*[:=]\s*\S+|"
    r"-----BEGIN [^-]+ PRIVATE KEY-----)"
)
_REQUIRED_ITEM_FIELDS: Final = frozenset(
    {"queue_id", "run_id", "target_id", "trigger", "mode", "requested_at", "status", "attempts"}
)
_OPTIONAL_ITEM_FIELDS: Final = frozenset({"failure_reason"})
_MAX_ATTEMPTS: Final = 1_000_000


class QueueError(ValueError):
    """Base error for invalid queue state or an invalid queue operation."""


class QueueValidationError(QueueError):
    """Raised when a path, input item, or persisted document is unsafe."""


class QueueOperationError(QueueError):
    """Raised when an operation does not match the persisted queue state."""


@dataclass(frozen=True)
class QueueItem:
    """One persisted queue entry.

    ``failure_reason`` is optional terminal metadata.  It is omitted from the
    JSON object for queued, running, and completed items, keeping the public
    queue item shape limited to the required fields in those states.
    """

    queue_id: str
    run_id: str
    target_id: str
    trigger: str
    mode: str
    requested_at: str
    status: str
    attempts: int
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the item using the versioned public queue shape."""

        value: dict[str, object] = {
            "queue_id": self.queue_id,
            "run_id": self.run_id,
            "target_id": self.target_id,
            "trigger": self.trigger,
            "mode": self.mode,
            "requested_at": self.requested_at,
            "status": self.status,
            "attempts": self.attempts,
        }
        if self.failure_reason is not None:
            value["failure_reason"] = self.failure_reason
        return value


_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _validate_queue_path(value: object) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise QueueValidationError("queue path must be a filesystem path")
    try:
        raw = os.fspath(value)
    except TypeError as error:
        raise QueueValidationError("queue path must be a filesystem path") from error
    if isinstance(raw, bytes):
        raise QueueValidationError("queue path must be text")
    if not raw.strip():
        raise QueueValidationError("queue path is required")
    candidate = Path(raw)
    if not candidate.name or candidate.name in {".", ".."}:
        raise QueueValidationError("queue path must name a file")
    if any(part == ".." for part in candidate.parts):
        raise QueueValidationError("queue path traversal is not allowed")

    # A symlink in the path could redirect an apparently safe queue outside
    # the caller's intended directory.  Reject it before reading or writing.
    try:
        current = candidate.parent
        while current != current.parent:
            if current.is_symlink():
                raise QueueValidationError("queue path traversal is not allowed")
            current = current.parent
        if candidate.is_symlink():
            raise QueueValidationError("queue path traversal is not allowed")
        if candidate.exists() and candidate.is_dir():
            raise QueueValidationError("queue path must name a file")
        return candidate.resolve()
    except OSError as error:
        raise QueueValidationError("queue path cannot be inspected") from error


def _reject_json_constant(value: str) -> None:
    raise QueueValidationError(f"non-standard JSON value {value!r} is not allowed")


def _object_pairs_without_duplicates(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise QueueValidationError("duplicate JSON fields are not allowed")
        result[key] = value
    return result


def _assert_no_secret_material(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise QueueValidationError("JSON object keys must be strings")
            if _SECRET_KEY_RE.search(key):
                raise QueueValidationError("secret-like fields are not allowed in the queue")
            _assert_no_secret_material(nested)
        return
    if isinstance(value, list):
        for nested in value:
            _assert_no_secret_material(nested)
        return
    if isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        raise QueueValidationError("secret-like values are not allowed in the queue")


def _validate_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise QueueValidationError(f"{field} is invalid")
    return value


def _validate_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise QueueValidationError("requested_at must be an ISO 8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise QueueValidationError("requested_at must be an ISO 8601 timestamp with timezone") from error
    if parsed.tzinfo is None:
        raise QueueValidationError("requested_at must include a timezone")
    return value


def _validate_reason(value: object) -> str:
    if not isinstance(value, str) or not _REASON_RE.fullmatch(value):
        raise QueueValidationError("failure reason must be a safe machine code")
    return value


def _item_from_mapping(value: object) -> QueueItem:
    if not isinstance(value, Mapping):
        raise QueueValidationError("queue items must be objects")
    keys = set(value)
    allowed = _REQUIRED_ITEM_FIELDS | _OPTIONAL_ITEM_FIELDS
    if not _REQUIRED_ITEM_FIELDS <= keys or not keys <= allowed:
        raise QueueValidationError("queue item has unexpected or missing fields")

    trigger = value["trigger"]
    if not isinstance(trigger, str) or trigger not in QUEUE_TRIGGERS:
        raise QueueValidationError("trigger must be manual or daily")
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in QUEUE_MODES:
        raise QueueValidationError("mode is invalid")
    status = value["status"]
    if not isinstance(status, str) or status not in QUEUE_STATUSES:
        raise QueueValidationError("status is invalid")
    attempts = value["attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= _MAX_ATTEMPTS:
        raise QueueValidationError("attempts must be a non-negative bounded integer")

    failure_reason = value.get("failure_reason")
    if failure_reason is not None:
        failure_reason = _validate_reason(failure_reason)
        if status != "failed":
            raise QueueValidationError("failure_reason is only valid for failed items")

    return QueueItem(
        queue_id=_validate_id(value["queue_id"], "queue_id"),
        run_id=_validate_id(value["run_id"], "run_id"),
        target_id=_validate_id(value["target_id"], "target_id"),
        trigger=trigger,
        mode=mode,
        requested_at=_validate_timestamp(value["requested_at"]),
        status=status,
        attempts=attempts,
        failure_reason=failure_reason,
    )


def _items_from_document(value: object) -> list[QueueItem]:
    if not isinstance(value, Mapping) or set(value) != {"schema", "items"}:
        raise QueueValidationError("queue document has an invalid shape")
    if value.get("schema") != QUEUE_SCHEMA:
        raise QueueValidationError("queue schema is invalid")
    raw_items = value.get("items")
    if not isinstance(raw_items, list):
        raise QueueValidationError("queue items must be a list")

    items = [_item_from_mapping(raw_item) for raw_item in raw_items]
    queue_ids = [item.queue_id for item in items]
    if len(queue_ids) != len(set(queue_ids)):
        raise QueueValidationError("queue_id values must be unique")
    active_keys = [(item.target_id, item.trigger) for item in items if item.status in {"queued", "running"}]
    if len(active_keys) != len(set(active_keys)):
        raise QueueValidationError("active target and trigger entries must be unique")
    if sum(item.status == "running" for item in items) > 1:
        raise QueueValidationError("only one queue item may be running")
    return items


def _decode_document(raw: str) -> list[QueueItem]:
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_object_pairs_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except QueueValidationError:
        raise
    except (json.JSONDecodeError, TypeError) as error:
        raise QueueValidationError("queue file contains malformed JSON") from error
    _assert_no_secret_material(document)
    return _items_from_document(document)


def _encode_document(items: list[QueueItem]) -> bytes:
    document = {"schema": QUEUE_SCHEMA, "items": [item.to_dict() for item in items]}
    _assert_no_secret_material(document)
    _items_from_document(document)
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


class SeoEmployeeQueue:
    """Persistent FIFO queue with one process-local mutex per file path."""

    def __init__(self, path: Path | str) -> None:
        self.path = _validate_queue_path(path)
        self._lock = _path_lock(self.path)
        with self._lock:
            if self.path.exists():
                self._read_items_unlocked()

    def _read_items_unlocked(self, *, allow_missing: bool = False) -> list[QueueItem]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if allow_missing:
                return []
            raise QueueValidationError("queue file cannot be read") from None
        except (OSError, UnicodeError) as error:
            raise QueueValidationError("queue file cannot be read") from error
        return _decode_document(raw)

    def _write_items_unlocked(self, items: list[QueueItem]) -> None:
        # Serialize/validate before creating the temporary file so rejected
        # state cannot replace the last known-good document.
        payload = _encode_document(items)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _recover(items: list[QueueItem]) -> list[QueueItem]:
        recovered: list[QueueItem] = []
        for item in items:
            if item.status != "running":
                recovered.append(item)
                continue
            if item.attempts == 0:
                recovered.append(replace(item, status="queued", attempts=1, failure_reason=None))
            else:
                recovered.append(
                    replace(
                        item,
                        status="failed",
                        attempts=min(item.attempts + 1, _MAX_ATTEMPTS),
                        failure_reason="recovery_limit",
                    )
                )
        return recovered

    @staticmethod
    def _validate_queue_id(value: object) -> str:
        return _validate_id(value, "queue_id")

    def snapshot(self) -> tuple[QueueItem, ...]:
        """Return the current persisted insertion order."""

        with self._lock:
            return tuple(self._read_items_unlocked(allow_missing=True))

    def items(self) -> tuple[QueueItem, ...]:
        """Alias for :meth:`snapshot` for callers that prefer collection wording."""

        return self.snapshot()

    def get(self, queue_id: str) -> QueueItem | None:
        queue_id = self._validate_queue_id(queue_id)
        with self._lock:
            return next((item for item in self._read_items_unlocked(allow_missing=True) if item.queue_id == queue_id), None)

    def enqueue(
        self,
        run_id: str,
        target_id: str,
        trigger: str,
        mode: str,
        requested_at: str,
        queue_id: str | None = None,
    ) -> QueueItem:
        """Append an item, or return the existing active item for its key."""

        run_id = _validate_id(run_id, "run_id")
        target_id = _validate_id(target_id, "target_id")
        if not isinstance(trigger, str) or trigger not in QUEUE_TRIGGERS:
            raise QueueValidationError("trigger must be manual or daily")
        if not isinstance(mode, str) or mode not in QUEUE_MODES:
            raise QueueValidationError("mode is invalid")
        requested_at = _validate_timestamp(requested_at)
        if queue_id is not None:
            queue_id = self._validate_queue_id(queue_id)

        with self._lock:
            items = self._read_items_unlocked(allow_missing=True)
            existing = next(
                (item for item in items if item.status in {"queued", "running"} and item.target_id == target_id and item.trigger == trigger),
                None,
            )
            if existing is not None:
                return existing

            terminal = [item for item in items if item.status not in {"queued", "running"}]
            if len(terminal) >= MAX_TERMINAL_ITEMS:
                retained_ids = {item.queue_id for item in terminal[-(MAX_TERMINAL_ITEMS - 1):]}
                items = [
                    item for item in items
                    if item.status in {"queued", "running"} or item.queue_id in retained_ids
                ]

            existing_ids = {item.queue_id for item in items}
            if queue_id is None:
                while True:
                    generated = f"queue-{uuid.uuid4().hex}"
                    if generated not in existing_ids:
                        queue_id = generated
                        break
            elif queue_id in existing_ids:
                raise QueueValidationError("queue_id already exists")

            item = QueueItem(
                queue_id=queue_id,
                run_id=run_id,
                target_id=target_id,
                trigger=trigger,
                mode=mode,
                requested_at=requested_at,
                status="queued",
                attempts=0,
            )
            self._write_items_unlocked([*items, item])
            return item

    def claim_next(self) -> QueueItem | None:
        """Atomically claim the first queued item when no item is running."""

        with self._lock:
            items = self._read_items_unlocked(allow_missing=True)
            if any(item.status == "running" for item in items):
                return None
            for index, item in enumerate(items):
                if item.status == "queued":
                    claimed = replace(item, status="running", failure_reason=None)
                    items[index] = claimed
                    self._write_items_unlocked(items)
                    return claimed
            return None

    def recover_interrupted(self) -> tuple[QueueItem, ...]:
        """Recover persisted running work once at explicit process startup."""

        with self._lock:
            items = self._read_items_unlocked(allow_missing=True)
            recovered = self._recover(items)
            if recovered != items:
                self._write_items_unlocked(recovered)
            return tuple(recovered)

    def complete(self, queue_id: str) -> QueueItem:
        """Mark a matching running item completed."""

        queue_id = self._validate_queue_id(queue_id)
        with self._lock:
            items = self._read_items_unlocked()
            for index, item in enumerate(items):
                if item.queue_id == queue_id:
                    if item.status != "running":
                        raise QueueOperationError("complete requires a matching running queue item")
                    completed = replace(item, status="completed", failure_reason=None)
                    items[index] = completed
                    self._write_items_unlocked(items)
                    return completed
            raise QueueOperationError("complete requires a matching running queue item")

    def fail(self, queue_id: str, reason: str = "failed") -> QueueItem:
        """Mark a matching running item failed with a safe machine reason."""

        queue_id = self._validate_queue_id(queue_id)
        reason = _validate_reason(reason)
        with self._lock:
            items = self._read_items_unlocked()
            for index, item in enumerate(items):
                if item.queue_id == queue_id:
                    if item.status != "running":
                        raise QueueOperationError("fail requires a matching running queue item")
                    failed = replace(item, status="failed", failure_reason=reason)
                    items[index] = failed
                    self._write_items_unlocked(items)
                    return failed
            raise QueueOperationError("fail requires a matching running queue item")


__all__ = [
    "QUEUE_MODES",
    "QUEUE_SCHEMA",
    "QUEUE_STATUSES",
    "QUEUE_TRIGGERS",
    "QueueError",
    "QueueItem",
    "QueueOperationError",
    "QueueValidationError",
    "SeoEmployeeQueue",
]
