"""Payload-free journal timings. No SQL, caller identities or exception text."""

import json
import logging
import sqlite3
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .errors import AdapterError

# Uvicorn configures this logger at INFO in the adapter's existing entrypoint.
LOGGER = logging.getLogger("uvicorn.error")
OPERATIONS = frozenset(
    {
        "initialize",
        "probe",
        "begin",
        "state",
        "prepare_write",
        "record",
        "complete",
        "lock_acquire",
        "lock_release",
    }
)


def emit(event, *, error=False):
    try:
        LOGGER.log(logging.WARNING if error else logging.INFO, json.dumps(event, sort_keys=True))
    except Exception:
        # Observability must not turn a persisted write into an HTTP failure.
        pass


def sqlite_error(exc):
    code = getattr(exc, "sqlite_errorcode", None)
    primary = code & 0xFF if isinstance(code, int) else None
    categories = {
        sqlite3.SQLITE_BUSY: "busy",
        sqlite3.SQLITE_LOCKED: "locked",
        sqlite3.SQLITE_FULL: "full",
        sqlite3.SQLITE_IOERR: "io_error",
        sqlite3.SQLITE_READONLY: "readonly",
        sqlite3.SQLITE_CORRUPT: "corrupt",
        sqlite3.SQLITE_NOTADB: "not_database",
        sqlite3.SQLITE_CANTOPEN: "cannot_open",
        sqlite3.SQLITE_CONSTRAINT: "constraint",
    }
    return categories.get(primary, "other"), code if isinstance(code, int) else None


@dataclass
class AddTiming:
    trace: str = field(default_factory=lambda: uuid.uuid4().hex)
    operations: dict = field(default_factory=dict)
    errors: Counter = field(default_factory=Counter)
    phases: Counter = field(default_factory=Counter)
    journal_ms: float = 0

    def record(self, event):
        operation = self.operations.setdefault(
            event["operation"], {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0}
        )
        operation["count"] += 1
        operation["errors"] += event["outcome"] == "error"
        operation["total_ms"] += event["elapsed_ms"]
        operation["max_ms"] = max(operation["max_ms"], event["elapsed_ms"])
        self.journal_ms += event["elapsed_ms"]
        for phase, value in event["phase_ms"].items():
            self.phases[phase] += value
        if event.get("error_category"):
            self.errors[event["error_category"]] += 1


CURRENT_ADD: ContextVar[AddTiming | None] = ContextVar("journal_add_timing", default=None)


@dataclass
class OperationTiming:
    phases: Counter = field(default_factory=Counter)
    counts: Counter = field(default_factory=Counter)
    error_category: str | None = None
    error_code: int | None = None
    failed_phase: str | None = None

    def run(self, phase, function, *args, **kwargs):
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        except sqlite3.Error as exc:
            if self.error_category is None:
                self.error_category, self.error_code = sqlite_error(exc)
                self.failed_phase = phase
            raise
        finally:
            self.phases[phase] += (time.perf_counter() - started) * 1000
            self.counts[phase] += 1


class JournalMetrics:
    def __init__(self, *, log_all_operations=False, slow_ms=100):
        self.log_all_operations = log_all_operations
        self.slow_ms = slow_ms

    @contextmanager
    def operation(self, operation):
        if operation not in OPERATIONS:
            raise ValueError("Unknown journal operation")
        timing = OperationTiming()
        started = time.perf_counter()
        failed = False
        try:
            yield timing
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            event = {
                "event": "journal_operation",
                "operation": operation,
                "outcome": "error" if failed else "ok",
                "elapsed_ms": round(elapsed, 6),
                "phase_ms": {k: round(v, 6) for k, v in timing.phases.items()},
                "phase_calls": dict(timing.counts),
            }
            if timing.error_category:
                event.update(
                    error_category=timing.error_category,
                    sqlite_error_code=timing.error_code,
                    failed_phase=timing.failed_phase,
                )
            current = CURRENT_ADD.get()
            if current is not None:
                event["add_trace"] = current.trace
                current.record(event)
            if self.log_all_operations or failed or elapsed >= self.slow_ms:
                emit(event, error=failed)

    @contextmanager
    def add(self):
        timing = AddTiming()
        token = CURRENT_ADD.set(timing)
        started = time.perf_counter()
        status = 200
        try:
            yield
        except BaseException as exc:
            status = exc.status if isinstance(exc, AdapterError) else 500
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            CURRENT_ADD.reset(token)
            emit(
                {
                    "event": "journal_add_summary",
                    "add_trace": timing.trace,
                    "outcome": "ok" if status == 200 else "error",
                    "http_status": status,
                    "add_elapsed_ms": round(elapsed, 6),
                    "journal_elapsed_ms": round(timing.journal_ms, 6),
                    "journal_fraction": round(timing.journal_ms / elapsed, 6) if elapsed else 0,
                    "operations": timing.operations,
                    "phase_ms": {k: round(v, 6) for k, v in timing.phases.items()},
                    "error_counts": dict(timing.errors),
                },
                error=status != 200,
            )
