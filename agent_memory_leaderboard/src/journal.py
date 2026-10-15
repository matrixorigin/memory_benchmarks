"""Durable idempotency state, with process-safe locks on a shared local disk."""

import fcntl
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import AdapterError

LEGACY_PROTOCOL = "legacy"
IDEMPOTENT_PROTOCOL = "idempotent_v1"


class Journal:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.locks = directory / "locks"
        self.locks.mkdir(exist_ok=True)
        self.path = directory / "requests.sqlite3"
        with self.connect() as db:
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
    def connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5)
            db.execute("PRAGMA synchronous = FULL")
            with db:
                yield db
        except sqlite3.Error as exc:
            raise AdapterError(503, "Idempotency journal unavailable") from exc
        finally:
            if db is not None:
                db.close()

    @contextmanager
    def lock(self, key: str):
        # Never delete lock files: unlinking a locked inode allows a second owner.
        try:
            handle = (self.locks / key).open("a")
        except OSError as exc:
            raise AdapterError(503, "Idempotency lock unavailable; check state storage") from exc
        with handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AdapterError(409, "This Add request is still processing") from exc
            except OSError as exc:
                raise AdapterError(
                    503, "Idempotency lock unavailable; check state storage"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def begin(self, key: str, fingerprint: str) -> bool:
        with self.connect() as db:
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
        with self.connect() as db:
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
        with self.connect() as db:
            db.execute(
                "INSERT INTO writes(request_key, position, state, memory_id, protocol) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(request_key, position) DO UPDATE SET "
                "state = excluded.state, memory_id = excluded.memory_id, "
                "protocol = excluded.protocol",
                (key, position, state, memory_id, protocol),
            )

    def complete(self, key: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE requests SET complete = 1 WHERE key = ?", (key,))
