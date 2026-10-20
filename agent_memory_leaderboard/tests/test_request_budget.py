"""Synthetic request budget and durable recovery checks."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from test_endpoints import FakeMemoria, addition, running

from src import budget
from src.budget import RequestBudgetExceeded, remaining, request_budget
from src.config import Settings
from src.journal import Journal
from src.memoria import IDEMPOTENT_PATH, Memoria
from src.models import AddRequest
from src.service import MemoryAdapter


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        state_dir=tmp_path,
        request_timeout_seconds=1,
    )
    upstream = FakeMemoria()
    return settings, upstream


def test_timeout_defaults_and_environment(monkeypatch):
    monkeypatch.setenv("MEMORIA_URL", "http://memoria.test")
    monkeypatch.setenv("MEMORIA_MASTER_KEY", "synthetic")
    settings = Settings.from_env()
    assert settings.connect_timeout_seconds == settings.journal_queue_timeout_seconds == 30
    assert settings.visibility_timeout_seconds == 60
    assert settings.request_timeout_seconds == 1700
    monkeypatch.setenv("MEMORIA_CONNECT_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("ADAPTER_REQUEST_TIMEOUT_SECONDS", "1600")
    settings = Settings.from_env()
    assert settings.connect_timeout_seconds == 45 and settings.request_timeout_seconds == 1600


@pytest.mark.parametrize(
    "field,maximum", [("connect_timeout_seconds", 120), ("request_timeout_seconds", 1740)]
)
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 1800])
def test_invalid_budget_rejected(setup, field, maximum, value):
    with pytest.raises(ValueError):
        replace(setup[0], **{field: value})


def test_nested_budget_cannot_extend_parent(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    with request_budget(1):
        clock[0] += 0.4
        with request_budget(10):
            assert remaining(20) == pytest.approx(0.6)
            clock[0] += 0.7
            with pytest.raises(RequestBudgetExceeded):
                remaining(20)
    assert remaining(20) == 20


def test_budget_spans_chunks_and_retry_preserves_receipts(setup, monkeypatch):
    settings, upstream = setup
    clock = [100.0]
    monkeypatch.setattr(budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def consume_time(request, *_):
        if request.url.path == IDEMPOTENT_PATH:
            clock[0] += 0.6

    upstream.hook = consume_time
    payload = AddRequest.model_validate(
        addition(messages=[{"role": "user", "content": f"Synthetic fact {i}"} for i in range(3)])
    )
    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        adapter = MemoryAdapter(settings, Memoria(settings, client), Journal(settings.state_dir))
        with pytest.raises(RequestBudgetExceeded):
            adapter.add(payload)
        assert len(upstream.writes) == 2  # No third write after shared budget expires.
        with sqlite3.connect(adapter.journal.path) as db:
            assert db.execute("SELECT complete FROM requests").fetchone() == (0,)
        upstream.hook = None
        adapter.add(payload)
        assert len(upstream.memories) == 3
        assert len(upstream.receipts) == 3


def test_journal_wait_consumes_request_budget_without_leaking_lock(setup):
    journal = Journal(setup[0].state_dir, queue_timeout_seconds=30)
    journal._connection_mutex.acquire()
    try:
        with request_budget(0.03), pytest.raises(RequestBudgetExceeded):
            journal.state("synthetic", 0)
    finally:
        journal._connection_mutex.release()
    assert journal.begin("synthetic", "fingerprint") is False


def test_admission_budget_expires_while_original_write_keeps_its_permit(setup):
    settings, upstream = setup
    settings = replace(settings, max_active_requests=1, request_timeout_seconds=0.05)
    entered, release = threading.Event(), threading.Event()

    def block(request, *_):
        if request.url.path == IDEMPOTENT_PATH:
            entered.set()
            assert release.wait(3)

    upstream.hook = block
    with running(settings, upstream) as client, ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(client.post, "/add", json=addition())
        try:
            assert entered.wait(2)
            waiting = client.post("/add", json=addition(request_id="other-synthetic-request"))
            assert waiting.status_code == 504
            assert not original.done()
            assert client.get("/health").status_code == 200
        finally:
            release.set()
        assert original.result(timeout=3).status_code == 504
        upstream.hook = None
        assert client.post("/add", json=addition()).status_code == 200
    assert len(upstream.memories) == 1


def test_connection_and_io_timeouts_are_separate(setup):
    settings, upstream = setup
    seen = []

    def inspect_timeout(request, *_):
        seen.append(request.extensions["timeout"])

    upstream.hook = inspect_timeout
    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        Memoria(settings, client).search("a" * 64, "synthetic", 1)
    assert seen[0] == {"connect": 30, "pool": 30, "read": 120, "write": 120}


def test_expiry_after_final_commit_preserves_completed_replay(setup, monkeypatch):
    settings, upstream = setup
    clock = [100.0]
    monkeypatch.setattr(budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    journal = Journal(settings.state_dir)
    original = sqlite3.connect
    commits = 0

    class SlowFinalCommit(sqlite3.Connection):
        def commit(self):
            nonlocal commits
            super().commit()
            commits += 1
            if commits == 3:
                clock[0] += 2

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: original(*a, factory=SlowFinalCommit, **kw)
    )
    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        adapter = MemoryAdapter(settings, Memoria(settings, client), journal)
        request = AddRequest.model_validate(addition())
        with pytest.raises(RequestBudgetExceeded):
            adapter.add(request)
        with sqlite3.connect(journal.path) as db:
            assert db.execute("SELECT complete FROM requests").fetchone() == (1,)
        calls = len(upstream.calls)
        adapter.add(request)
        assert len(upstream.calls) == calls
