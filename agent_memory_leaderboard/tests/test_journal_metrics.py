import json
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from src import journal_metrics
from src.config import Settings
from src.errors import AdapterError
from src.journal import IDEMPOTENT_PROTOCOL, Journal
from src.journal_metrics import CURRENT_ADD
from tests.test_endpoints import FakeMemoria, addition, running


def events(caplog, kind):
    return [
        value
        for record in caplog.records
        if record.name == "uvicorn.error"
        and (value := json.loads(record.getMessage()))["event"] == kind
    ]


def test_default_add_summary_includes_idempotent_replay_without_payloads(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    settings = Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="synthetic-backend",
        state_dir=tmp_path,
        journal_slow_ms=1e9,
    )
    payload = addition(
        user_id="synthetic-private-user",
        request_id="synthetic-private-request",
        session_id="synthetic-private-session",
        messages=[{"role": "user", "content": "synthetic-private-message"}],
    )
    upstream = FakeMemoria()
    # FakeMemoria checks this fixture's backend credential internally.
    settings = replace(settings, memoria_master_key="upstream-secret")
    with running(settings, upstream) as client:
        assert client.post("/add", json=payload).status_code == 200
        assert client.post("/add", json=payload).status_code == 200
        assert (
            client.post("/add", json=payload | {"session_id": "conflicting-session"}).status_code
            == 400
        )
    summaries = events(caplog, "journal_add_summary")
    assert [e["http_status"] for e in summaries] == [200, 200, 400]
    assert summaries[0]["operations"]["record"]["count"] == 2
    assert summaries[1]["operations"]["state"]["count"] == 1
    assert "prepare_write" not in summaries[1]["operations"]
    assert "record" not in summaries[1]["operations"]
    assert len({e["add_trace"] for e in summaries}) == 3
    assert all(0 <= e["journal_fraction"] <= 1.001 for e in summaries)
    assert all(e["journal_elapsed_ms"] <= e["add_elapsed_ms"] + 0.001 for e in summaries)
    # Successful fast operations are omitted, but the conflict is always logged.
    operations = events(caplog, "journal_operation")
    assert len(operations) == 1 and operations[0]["outcome"] == "error"
    serialized = json.dumps(summaries + operations)
    for value in [
        *[payload[k] for k in ("user_id", "request_id", "session_id")],
        "synthetic-private-message",
        "upstream-secret",
        "memoria.test",
        str(tmp_path),
        "SELECT",
        "INSERT",
        "UPDATE",
        "fingerprint",
    ]:
        assert value not in serialized
    assert len(upstream.writes) == 1


def test_sql_and_commit_timings_are_measured_separately(tmp_path, monkeypatch, caplog):
    clock = [0.0]
    original_connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, statement, *args):
            clock[0] += 0.002 if statement.startswith("PRAGMA") else 0.010
            return super().execute(statement, *args)

        def commit(self):
            clock[0] += 0.030
            return super().commit()

    def connect(*args, **kwargs):
        clock[0] += 0.001
        return original_connect(*args, factory=Connection, **kwargs)

    monkeypatch.setattr(journal_metrics.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(sqlite3, "connect", connect)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    journal = Journal(tmp_path, log_all_operations=True)
    caplog.clear()
    assert journal.begin("private-request", "private-fingerprint") is False
    (event,) = events(caplog, "journal_operation")
    assert event["phase_ms"]["sql"] == pytest.approx(20)
    assert event["phase_ms"]["commit"] == pytest.approx(30)
    assert event["phase_ms"]["setup"] == pytest.approx(2)
    assert event["phase_ms"]["connect"] == pytest.approx(1)
    assert event["elapsed_ms"] == pytest.approx(53)
    assert event["phase_calls"]["sql"] == 2
    assert event["phase_calls"]["commit"] == 1


def test_slow_operations_are_logged_without_verbose_mode(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    journal = Journal(tmp_path, slow_ms=0)
    caplog.clear()
    journal.state("private-request", 0)
    (event,) = events(caplog, "journal_operation")
    assert event["operation"] == "state" and event["outcome"] == "ok"
    assert "commit" not in event["phase_calls"]  # Reads do not start a transaction.


def test_sqlite_busy_is_classified_and_failed_attempt_is_summarized(
    tmp_path,
    monkeypatch,
    caplog,
):
    journal = Journal(tmp_path, slow_ms=1e9)
    original_connect = sqlite3.connect
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    def short_timeout(*args, **kwargs):
        return original_connect(*args, **(kwargs | {"timeout": 0.01}))

    with original_connect(journal.path) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(sqlite3, "connect", short_timeout)
        with pytest.raises(AdapterError) as caught, journal.metrics.add():
            journal.begin("private-request", "private-fingerprint")
        assert caught.value.status == 503
    (event,) = events(caplog, "journal_operation")
    assert event["error_category"] == "busy"
    assert event["sqlite_error_code"] & 0xFF == sqlite3.SQLITE_BUSY
    assert event["failed_phase"] == "sql"
    (summary,) = events(caplog, "journal_add_summary")
    assert summary["http_status"] == 503
    assert summary["error_counts"] == {"busy": 1}
    assert CURRENT_ADD.get() is None


def test_failed_commit_rolls_back_and_preserves_safe_error(tmp_path, monkeypatch, caplog):
    original_connect = sqlite3.connect
    rollbacks = []
    journal = Journal(tmp_path, slow_ms=1e9)

    class Connection(sqlite3.Connection):
        def commit(self):
            exc = sqlite3.OperationalError("synthetic-private-exception")
            exc.sqlite_errorcode = sqlite3.SQLITE_IOERR_FSYNC
            raise exc

        def rollback(self):
            rollbacks.append(True)
            return super().rollback()

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: original_connect(*a, factory=Connection, **kw)
    )
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    with pytest.raises(AdapterError) as caught:
        journal.begin("private-request", "private-fingerprint")
    assert caught.value.status == 503 and rollbacks == [True]
    with original_connect(journal.path) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone() == (0,)
    (event,) = events(caplog, "journal_operation")
    assert event["error_category"] == "io_error" and event["failed_phase"] == "commit"
    assert event["phase_calls"]["commit"] == event["phase_calls"]["rollback"] == 1
    assert "synthetic-private-exception" not in caplog.text


def test_body_exception_rolls_back_without_logging_exception_text(tmp_path, caplog):
    journal = Journal(tmp_path, slow_ms=1e9)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    with pytest.raises(RuntimeError), journal.connect("begin") as db:
        db.execute(
            "INSERT INTO requests(key, fingerprint, complete) VALUES (?, ?, 0)",
            ("private-request", "private-body"),
        )
        raise RuntimeError("synthetic-private-exception")
    with sqlite3.connect(journal.path) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone() == (0,)
    (event,) = events(caplog, "journal_operation")
    assert event["phase_calls"]["rollback"] == 1
    assert "commit" not in event["phase_calls"]
    assert "synthetic-private-exception" not in caplog.text


def test_schema_upgrade_commit_failure_rolls_back_and_is_logged(tmp_path, monkeypatch, caplog):
    original_connect = sqlite3.connect
    path = tmp_path / "requests.sqlite3"
    with original_connect(path) as db:
        db.execute(
            "CREATE TABLE writes(request_key TEXT, position INTEGER, "
            "state TEXT, memory_id TEXT, PRIMARY KEY(request_key, position))"
        )

    class Connection(sqlite3.Connection):
        def commit(self):
            exc = sqlite3.OperationalError("synthetic-private-upgrade-error")
            exc.sqlite_errorcode = sqlite3.SQLITE_IOERR_FSYNC
            raise exc

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: original_connect(*a, factory=Connection, **kw)
    )
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    with pytest.raises(AdapterError) as caught:
        Journal(tmp_path, slow_ms=1e9)
    assert caught.value.status == 503
    with original_connect(path) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(writes)")] == [
            "request_key",
            "position",
            "state",
            "memory_id",
        ]
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'requests'").fetchall() == []
    (event,) = events(caplog, "journal_operation")
    assert event["operation"] == "initialize"
    assert event["error_category"] == "io_error" and event["failed_phase"] == "commit"
    assert event["phase_calls"]["commit"] == event["phase_calls"]["rollback"] == 1
    assert "synthetic-private-upgrade-error" not in caplog.text


def test_concurrent_add_summaries_are_isolated(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    journal = Journal(tmp_path, log_all_operations=True)
    caplog.clear()
    barrier = threading.Barrier(16)

    def add(index):
        barrier.wait(timeout=10)
        with journal.metrics.add():
            key = f"private-{index}"
            with journal.lock(key):
                journal.begin(key, "private-fingerprint")
                journal.state(key, 0)
                journal.record(key, 0, "stored", "private-memory", protocol=IDEMPOTENT_PROTOCOL)
                journal.complete(key)
        assert CURRENT_ADD.get() is None

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(add, range(16)))
    summaries = events(caplog, "journal_add_summary")
    operations = events(caplog, "journal_operation")
    assert len(summaries) == len({s["add_trace"] for s in summaries}) == 16
    for summary in summaries:
        assert summary["http_status"] == 200
        assert set(summary["operations"]) == {
            "begin",
            "state",
            "record",
            "complete",
            "lock_acquire",
            "lock_release",
        }
        assert all(v["count"] == 1 for v in summary["operations"].values())
        assert sum(e["add_trace"] == summary["add_trace"] for e in operations) == 6
    assert CURRENT_ADD.get() is None


def test_request_lock_conflict_is_distinct_from_sqlite_busy(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    journal = Journal(tmp_path, slow_ms=1e9)
    with journal.lock("private-request"):
        with pytest.raises(AdapterError) as caught, journal.lock("private-request"):
            pytest.fail("same-request lock must not be acquired twice")
        assert caught.value.status == 409
    (event,) = events(caplog, "journal_operation")
    assert event["error_category"] == "request_lock_conflict"
    assert "private-request" not in caplog.text


def test_logging_failure_does_not_change_persistence_or_errors(tmp_path, monkeypatch):
    def broken_logger(*args, **kwargs):
        raise RuntimeError("synthetic-private-logger-error")

    monkeypatch.setattr(journal_metrics.LOGGER, "log", broken_logger)
    journal = Journal(tmp_path, log_all_operations=True)
    with journal.metrics.add():
        assert journal.begin("request", "fingerprint") is False
        journal.complete("request")
    assert journal.begin("request", "fingerprint") is True
    with pytest.raises(AdapterError) as caught, journal.metrics.add():
        journal.begin("request", "changed")
    assert caught.value.status == 400


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_slow_threshold_rejected(value):
    with pytest.raises(ValueError, match="ADAPTER_JOURNAL_SLOW_MS"):
        Settings(
            memoria_url="http://memoria.test", memoria_master_key="test", journal_slow_ms=value
        )


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("FALSE", False)])
def test_logging_configuration_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("MEMORIA_URL", "http://memoria.test")
    monkeypatch.setenv("MEMORIA_MASTER_KEY", "test")
    monkeypatch.setenv("ADAPTER_JOURNAL_LOG_ALL_OPERATIONS", value)
    monkeypatch.setenv("ADAPTER_JOURNAL_SLOW_MS", "25")
    settings = Settings.from_env()
    assert settings.journal_log_all_operations is expected
    assert settings.journal_slow_ms == 25
    monkeypatch.setenv("ADAPTER_JOURNAL_LOG_ALL_OPERATIONS", "invalid")
    with pytest.raises(ValueError, match="ADAPTER_JOURNAL_LOG_ALL_OPERATIONS"):
        Settings.from_env()
