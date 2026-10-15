"""Durable idempotency state, with process-safe locks on a shared local disk."""

import fcntl
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import AdapterError
from .journal_metrics import JournalMetrics

LEGACY_PROTOCOL = "legacy"
IDEMPOTENT_PROTOCOL = "idempotent_v1"


class TimedConnection:
    def __init__(self, db, timing):
        self.db = db
        self.timing = timing

    def execute(self, statement, parameters=()):
        return self.timing.run("sql", self.db.execute, statement, parameters)

    def executescript(self, script):
        return self.timing.run("sql", self.db.executescript, script)


class Journal:
    def __init__(self, directory: Path, *, log_all_operations=False, slow_ms=100):
        self.metrics = JournalMetrics(log_all_operations=log_all_operations, slow_ms=slow_ms)
        directory.mkdir(parents=True, exist_ok=True)
        self.locks = directory / "locks"
        self.locks.mkdir(exist_ok=True)
        self.path = directory / "requests.sqlite3"
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

    @contextmanager
    def connect(self, operation="probe"):
        with self.metrics.operation(operation) as timing:
            db = None
            try:
                db = timing.run("connect", sqlite3.connect, self.path, timeout=5)
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

    def begin(self, key: str, fingerprint: str) -> bool:
        with self.connect("begin") as db:
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

    def state(self, key: str, position: int) -> tuple[str, str | None, str]:
        with self.connect("state") as db:
            row = db.execute(
                "SELECT state, memory_id, protocol FROM writes "
                "WHERE request_key = ? AND position = ?",
                (key, position),
            ).fetchone()
            return row if row else ("pending", None, IDEMPOTENT_PROTOCOL)

    def record(
        self,
        key: str,
        position: int,
        state: str,
        memory_id: str | None = None,
        *,
        protocol: str,
    ) -> None:
        with self.connect("record") as db:
            db.execute(
                "INSERT INTO writes(request_key, position, state, memory_id, protocol) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(request_key, position) DO UPDATE SET "
                "state = excluded.state, memory_id = excluded.memory_id, "
                "protocol = excluded.protocol",
                (key, position, state, memory_id, protocol),
            )

    def complete(self, key: str) -> None:
        with self.connect("complete") as db:
            db.execute("UPDATE requests SET complete = 1 WHERE key = ?", (key,))
