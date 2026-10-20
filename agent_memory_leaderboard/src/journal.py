"""Durable idempotency state, with process-safe locks on a shared local disk."""

import fcntl
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .budget import RequestBudgetExceeded
from .budget import remaining as budget_remaining
from .errors import AdapterError
from .journal_metrics import JournalMetrics

LEGACY_PROTOCOL = "legacy"
IDEMPOTENT_PROTOCOL = "idempotent_v1"


class TimedConnection:
    def __init__(self, db, timing):
        self.db = db
        self.timing = timing

    def execute(self, statement, parameters=()):
        budget_remaining(float("inf"))
        return self.timing.run("sql", self.db.execute, statement, parameters)


class Journal:
    def __init__(
        self,
        directory: Path,
        *,
        log_all_operations=False,
        slow_ms=100,
        queue_timeout_seconds=30.0,
    ):
        if not math.isfinite(queue_timeout_seconds) or not 0 < queue_timeout_seconds <= 120:
            raise ValueError("Journal queue timeout must be in (0, 120]")
        self.queue_timeout_seconds = queue_timeout_seconds
        self.metrics = JournalMetrics(log_all_operations=log_all_operations, slow_ms=slow_ms)
        directory.mkdir(parents=True, exist_ok=True)
        self.locks = directory / "locks"
        self.locks.mkdir(exist_ok=True)
        self.path = directory / "requests.sqlite3"
        # A separate, never-unlinked inode coordinates short journal operations
        # across processes. The local mutex avoids a flock polling herd of threads.
        self.gate_path = directory / "sqlite.lock"
        self._connection_mutex = threading.Lock()
        with self.connect("initialize") as db:
            # Serialize schema inspection and upgrade across app processes. Existing
            # writes must remain legacy: metadata markers are not atomic receipts.
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS requests (
                    key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    complete INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS writes (
                    request_key TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    memory_id TEXT,
                    protocol TEXT NOT NULL DEFAULT 'legacy',
                    PRIMARY KEY (request_key, position)
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(writes)")}
            if "protocol" not in columns:
                db.execute("ALTER TABLE writes ADD COLUMN protocol TEXT NOT NULL DEFAULT 'legacy'")

    def _acquire_connection_slot(self):
        queue_budget = budget_remaining(self.queue_timeout_seconds)
        deadline = time.monotonic() + queue_budget
        if not self._connection_mutex.acquire(timeout=queue_budget):
            raise TimeoutError
        handle = None
        try:
            handle = self.gate_path.open("a")
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return handle
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError from None
                    time.sleep(min(0.01, remaining))
        except BaseException:
            try:
                if handle is not None:
                    handle.close()
            finally:
                self._connection_mutex.release()
            raise

    @contextmanager
    def _connection_slot(self, timing):
        try:
            handle = timing.run("queue", self._acquire_connection_slot)
        except RequestBudgetExceeded:
            timing.error_category = "request_budget_exhausted"
            timing.failed_phase = "queue"
            raise
        except TimeoutError as exc:
            try:
                budget_remaining(self.queue_timeout_seconds)
            except RequestBudgetExceeded:
                timing.error_category = "request_budget_exhausted"
                timing.failed_phase = "queue"
                raise
            timing.error_category = "journal_queue_timeout"
            timing.failed_phase = "queue"
            raise AdapterError(503, "Idempotency journal is busy; retry Add") from exc
        except OSError as exc:
            timing.error_category = "journal_lock_io_error"
            timing.failed_phase = "queue"
            raise AdapterError(503, "Idempotency journal lock unavailable") from exc
        try:
            yield
        finally:
            # Closing releases flock even after rollback/errors. Never unlink:
            # another process may already be waiting on this same inode.
            try:
                handle.close()
            finally:
                self._connection_mutex.release()

    @contextmanager
    def connect(self, operation="probe"):
        with self.metrics.operation(operation) as timing, self._connection_slot(timing):
            db = None
            try:
                db = timing.run("connect", sqlite3.connect, self.path, timeout=budget_remaining(5))
                timing.run("setup", db.execute, "PRAGMA synchronous = FULL")
                try:
                    yield TimedConnection(db, timing)
                except BaseException:
                    if db.in_transaction:
                        timing.run("rollback", db.rollback)
                    raise
                else:
                    if db.in_transaction:
                        try:
                            timing.run("commit", db.commit)
                        except BaseException:
                            # Match sqlite's context manager: a failed commit
                            # must roll back before this connection is closed.
                            timing.run("rollback", db.rollback)
                            raise
            except sqlite3.Error as exc:
                raise AdapterError(503, "Idempotency journal unavailable") from exc
            finally:
                if db is not None:
                    timing.run("close", db.close)

    def probe(self) -> None:
        # A read-only probe must not join the writer queue or configure SQLite.
        # BUSY/corruption/missing storage still fail closed, with a short wait.
        with self.metrics.operation("probe") as timing:
            db = None
            try:
                uri = self.path.absolute().as_uri() + "?mode=ro"
                db = timing.run("connect", sqlite3.connect, uri, uri=True, timeout=0.05)
                timing.run("sql", db.execute, "SELECT key FROM requests LIMIT 1").fetchone()
            except sqlite3.Error as exc:
                raise AdapterError(503, "Idempotency journal unavailable") from exc
            finally:
                if db is not None:
                    timing.run("close", db.close)

    @contextmanager
    def lock(self, key: str):
        # Never delete lock files: unlinking a locked inode allows a second owner.
        with self.metrics.operation("lock_acquire") as timing:
            try:
                handle = (self.locks / key).open("a")
            except OSError as exc:
                timing.error_category = "lock_io_error"
                raise AdapterError(
                    503, "Idempotency lock unavailable; check state storage"
                ) from exc
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                timing.error_category = "request_lock_conflict"
                raise AdapterError(409, "This Add request is still processing") from exc
            except OSError as exc:
                handle.close()
                timing.error_category = "lock_io_error"
                raise AdapterError(
                    503, "Idempotency lock unavailable; check state storage"
                ) from exc
        with handle:
            try:
                yield
            finally:
                with self.metrics.operation("lock_release") as timing:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    except OSError:
                        timing.error_category = "lock_io_error"
                        raise

    @staticmethod
    def _begin(db, key: str, fingerprint: str) -> bool:
        row = db.execute(
            "SELECT fingerprint, complete FROM requests WHERE key = ?", (key,)
        ).fetchone()
        if row:
            if row[0] != fingerprint:
                raise AdapterError(
                    400,
                    "request_id was already used with a different payload; "
                    "restore the original payload",
                )
            return bool(row[1])
        db.execute("INSERT INTO requests(key, fingerprint) VALUES (?, ?)", (key, fingerprint))
        return False

    def begin(self, key: str, fingerprint: str) -> bool:
        with self.connect("begin") as db:
            return self._begin(db, key, fingerprint)

    @staticmethod
    def _state(db, key: str, position: int) -> tuple[str, str | None, str]:
        row = db.execute(
            "SELECT state, memory_id, protocol FROM writes WHERE request_key = ? AND position = ?",
            (key, position),
        ).fetchone()
        return row if row else ("pending", None, IDEMPOTENT_PROTOCOL)

    def state(self, key: str, position: int) -> tuple[str, str | None, str]:
        with self.connect("state") as db:
            return self._state(db, key, position)

    @staticmethod
    def _record(db, key, position, state, memory_id, protocol):
        db.execute(
            "INSERT INTO writes(request_key, position, state, memory_id, protocol) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(request_key, position) DO UPDATE SET "
            "state = excluded.state, memory_id = excluded.memory_id, "
            "protocol = excluded.protocol",
            (key, position, state, memory_id, protocol),
        )

    def prepare_write(
        self, key: str, fingerprint: str, position: int
    ) -> tuple[str, str | None, str] | None:
        """Register the request and persist send intent in one durable transaction.

        Caller holds lock(key) throughout Add. None denotes a completed replay.
        Legacy unknown outcomes must never be converted to atomic receipts.
        """
        with self.connect("prepare_write") as db:
            if self._begin(db, key, fingerprint):
                return None
            state, memory_id, protocol = self._state(db, key, position)
            if protocol not in {LEGACY_PROTOCOL, IDEMPOTENT_PROTOCOL}:
                raise AdapterError(503, "Unsupported journal write protocol; check adapter version")
            if state not in {"pending", "inflight", "stored"}:
                raise AdapterError(503, "Unsupported journal write state; check adapter version")
            if state == "pending":
                # Pending legacy writes are known not to have been sent. Persist
                # the atomic protocol before HTTP, including on the first chunk.
                state, memory_id, protocol = "inflight", None, IDEMPOTENT_PROTOCOL
                self._record(db, key, position, state, memory_id, protocol)
            return state, memory_id, protocol

    def record(
        self,
        key: str,
        position: int,
        state: str,
        memory_id: str | None = None,
        *,
        protocol: str,
        complete_request: bool = False,
    ) -> None:
        if complete_request and state != "stored":
            raise ValueError("Only a verified stored write can complete a request")
        with self.connect("record") as db:
            self._record(db, key, position, state, memory_id, protocol)
            if complete_request:
                db.execute("UPDATE requests SET complete = 1 WHERE key = ?", (key,))

    def complete(self, key: str) -> None:
        with self.connect("complete") as db:
            db.execute("UPDATE requests SET complete = 1 WHERE key = ?", (key,))
