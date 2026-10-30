import json
import logging
import multiprocessing
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.errors import AdapterError
from src.journal import IDEMPOTENT_PROTOCOL, Journal


def test_other_journal_instance_waits_before_opening_a_competing_sqlite_connection(
    tmp_path, monkeypatch
):
    holder = Journal(tmp_path)
    follower = Journal(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_connect = sqlite3.connect

    def short_sqlite_timeout(*args, **kwargs):
        return original_connect(*args, **(kwargs | {"timeout": 0.01}))

    monkeypatch.setattr(sqlite3, "connect", short_sqlite_timeout)

    def hold_write():
        with holder.connect("record") as db:
            db.execute(
                "INSERT INTO requests(key, fingerprint, complete) VALUES (?, ?, 0)",
                ("holder", "fingerprint"),
            )
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hold_write)
        assert entered.wait(5)
        second = pool.submit(follower.begin, "follower", "fingerprint")
        try:
            time.sleep(0.1)
            completed_while_locked = second.done()
        finally:
            release.set()
        first.result(timeout=5)
        assert second.result(timeout=5) is False
    assert not completed_while_locked
    with original_connect(holder.path) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone() == (2,)


def test_64_distinct_requests_with_slow_commits_preserve_all_journal_states(tmp_path, monkeypatch):
    journals = [Journal(tmp_path) for _ in range(4)]
    original_connect = sqlite3.connect

    class SlowConnection(sqlite3.Connection):
        def commit(self):
            time.sleep(0.005)
            return super().commit()

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(
            *args, **(kwargs | {"timeout": 0.01, "factory": SlowConnection})
        ),
    )
    barrier = threading.Barrier(64)

    def add(index):
        journal = journals[index % len(journals)]
        key = f"synthetic-{index}"
        barrier.wait(timeout=10)
        with journal.lock(key):
            assert journal.begin(key, "fingerprint") is False
            assert journal.state(key, 0) == ("pending", None, IDEMPOTENT_PROTOCOL)
            journal.record(key, 0, "inflight", protocol=IDEMPOTENT_PROTOCOL)
            journal.record(key, 0, "stored", f"memory-{index}", protocol=IDEMPOTENT_PROTOCOL)
            journal.complete(key)
        assert journal.begin(key, "fingerprint") is True

    with ThreadPoolExecutor(max_workers=64) as pool:
        list(pool.map(add, range(64)))
    with original_connect(journals[0].path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM requests WHERE complete=1").fetchone() == (64,)
        assert db.execute("SELECT count(*) FROM writes WHERE state='stored'").fetchone() == (64,)


def test_queue_timeout_does_not_open_sqlite_or_change_state_and_can_retry(
    tmp_path, monkeypatch, caplog
):
    holder = Journal(tmp_path)
    follower = Journal(tmp_path, log_all_operations=True, queue_timeout_seconds=0.02)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    original_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        opened.append(True)
        return original_connect(*args, **kwargs)

    with holder.connect("probe"):
        monkeypatch.setattr(sqlite3, "connect", connect)
        with pytest.raises(AdapterError) as caught:
            follower.begin("synthetic", "fingerprint")
        assert caught.value.status == 503 and not opened
    assert follower.begin("synthetic", "fingerprint") is False
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "uvicorn.error"]
    failure = next(e for e in events if e.get("error_category") == "journal_queue_timeout")
    assert failure["failed_phase"] == "queue" and failure["phase_ms"]["queue"] > 0
    assert failure["sqlite_error_code"] is None


def _child_holding_transaction(directory, entered):
    journal = Journal(directory)
    with journal.connect("record") as db:
        db.execute(
            "INSERT INTO requests(key, fingerprint, complete) VALUES (?, ?, 0)",
            ("uncommitted", "fingerprint"),
        )
        entered.set()
        time.sleep(60)


def test_process_exit_releases_gate_and_rolls_back_uncommitted_state(tmp_path):
    journal = Journal(tmp_path)
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=_child_holding_transaction, args=(tmp_path, entered))
    process.start()
    try:
        assert entered.wait(10)
    finally:
        process.terminate()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
    assert not process.is_alive()
    assert journal.begin("after-crash", "fingerprint") is False
    with sqlite3.connect(journal.path) as db:
        assert db.execute("SELECT key FROM requests").fetchall() == [("after-crash",)]
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_operation_failure_releases_slot_and_preserves_rollback(tmp_path):
    journal = Journal(tmp_path)
    with pytest.raises(RuntimeError), journal.connect("record") as db:
        db.execute(
            "INSERT INTO requests(key, fingerprint, complete) VALUES (?, ?, 0)",
            ("rolled-back", "fingerprint"),
        )
        raise RuntimeError("synthetic failure")
    assert journal.begin("rolled-back", "fingerprint") is False


def test_gate_file_error_is_safe_and_does_not_leak_local_mutex(tmp_path, monkeypatch, caplog):
    journal = Journal(tmp_path)
    original_open = type(journal.gate_path).open

    def fail_gate(path, *args, **kwargs):
        if path == journal.gate_path:
            raise PermissionError("synthetic-private-path")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(type(journal.gate_path), "open", fail_gate)
        with pytest.raises(AdapterError) as caught:
            journal.begin("synthetic", "fingerprint")
        assert caught.value.status == 503
    assert "synthetic-private-path" not in caplog.text
    assert journal.begin("synthetic", "fingerprint") is False


@pytest.mark.parametrize("value", [0, -1, 121, float("nan"), float("inf")])
def test_invalid_journal_queue_budget_rejected(tmp_path, value):
    from src.config import Settings

    with pytest.raises(ValueError, match="queue timeout"):
        Journal(tmp_path, queue_timeout_seconds=value)
    with pytest.raises(ValueError, match="ADAPTER_JOURNAL_QUEUE_TIMEOUT_SECONDS"):
        Settings(
            memoria_url="http://memoria.test",
            memoria_master_key="synthetic",
            journal_queue_timeout_seconds=value,
        )


def test_journal_queue_budget_from_environment(monkeypatch):
    from src.config import Settings

    monkeypatch.setenv("MEMORIA_URL", "http://memoria.test")
    monkeypatch.setenv("MEMORIA_MASTER_KEY", "synthetic")
    monkeypatch.setenv("ADAPTER_JOURNAL_QUEUE_TIMEOUT_SECONDS", "12.5")
    assert Settings.from_env().journal_queue_timeout_seconds == 12.5


def test_application_passes_queue_budget_to_journal(tmp_path, monkeypatch):
    import src.app as app_module
    from src.config import Settings

    budgets = []
    original = Journal

    def journal(*args, **kwargs):
        budgets.append(kwargs["queue_timeout_seconds"])
        return original(*args, **kwargs)

    monkeypatch.setattr(app_module, "Journal", journal)
    import httpx

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as http:
        app_module.create_app(
            Settings(
                memoria_url="http://memoria.test",
                memoria_master_key="synthetic",
                state_dir=tmp_path,
                journal_queue_timeout_seconds=12.5,
            ),
            client=http,
        )
    assert budgets == [12.5]
