"""Durable transaction boundaries exercised with synthetic payloads and failures."""

import sqlite3

import httpx
import pytest
from test_endpoints import FakeMemoria, addition

from src.config import Settings
from src.errors import AdapterError
from src.journal import IDEMPOTENT_PROTOCOL, TOKEN_PROTOCOL, Journal
from src.memoria import Memoria
from src.models import AddRequest
from src.service import MemoryAdapter, digest


@pytest.fixture
def adapter(tmp_path):
    settings = Settings(
        memoria_url="http://memoria.test", memoria_master_key="upstream-secret", state_dir=tmp_path
    )
    journal = Journal(tmp_path)
    upstream = FakeMemoria()
    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        yield MemoryAdapter(settings, Memoria(settings, http), journal), upstream


def snapshot(adapter):
    with sqlite3.connect(adapter.journal.path) as db:
        requests = db.execute("SELECT complete FROM requests").fetchall()
        writes = db.execute(
            "SELECT state, memory_id, protocol FROM writes ORDER BY position"
        ).fetchall()
    return requests, writes


@pytest.mark.parametrize("chunks", [1, 3])
def test_three_durable_commits_per_chunk_and_read_only_replay(adapter, monkeypatch, chunks):
    service, upstream = adapter
    original = sqlite3.connect
    commits = []

    class Counted(sqlite3.Connection):
        def commit(self):
            commits.append(True)
            return super().commit()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Counted, **kw))
    request = AddRequest.model_validate(
        addition(
            messages=[{"role": "user", "content": f"Synthetic fact {i}"} for i in range(chunks)]
        )
    )
    service.add(request)
    assert len(commits) == 3 * chunks
    assert len(upstream.memories) == chunks
    assert snapshot(service)[0] == [(1,)]
    service.add(request)
    assert len(commits) == 3 * chunks
    assert len(upstream.writes) == chunks


@pytest.mark.parametrize("failed_commit", [1, 2, 3])
def test_failed_commit_preserves_recoverable_state(adapter, monkeypatch, failed_commit):
    service, upstream = adapter
    original = sqlite3.connect
    attempts = 0

    class Failing(sqlite3.Connection):
        def commit(self):
            nonlocal attempts
            attempts += 1
            if attempts == failed_commit:
                raise sqlite3.OperationalError("synthetic commit failure")
            return super().commit()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Failing, **kw))
    request = AddRequest.model_validate(addition())
    with pytest.raises(AdapterError) as error:
        service.add(request)
    assert error.value.status == 503
    requests, writes = snapshot(service)
    if failed_commit == 1:
        assert requests == writes == []
        assert not upstream.writes
    else:
        assert requests == [(0,)]
        assert writes[0][0] == "inflight" and writes[0][2] == TOKEN_PROTOCOL
        assert (writes[0][1] is not None) == (failed_commit == 3)
    # Reopen the journal as on restart. A lost ACK can replay the same receipt;
    # a durable known ID must recover without issuing another write.
    service.journal = Journal(service.settings.state_dir)
    service.add(request)
    assert snapshot(service)[0] == [(1,)]
    assert len(upstream.memories) == 1
    assert len(upstream.writes) == (2 if failed_commit == 2 else 1)


def test_completion_sql_failure_rolls_back_last_stored_marker(adapter):
    service, upstream = adapter
    with sqlite3.connect(service.journal.path) as db:
        db.execute("""CREATE TRIGGER reject_complete BEFORE UPDATE OF complete ON requests
                      BEGIN SELECT RAISE(ABORT, 'synthetic completion failure'); END""")
    request = AddRequest.model_validate(addition())
    with pytest.raises(AdapterError):
        service.add(request)
    requests, writes = snapshot(service)
    assert requests == [(0,)]
    assert writes[0][0] == "inflight" and writes[0][1] is not None
    with sqlite3.connect(service.journal.path) as db:
        db.execute("DROP TRIGGER reject_complete")
    service.add(request)
    assert snapshot(service)[0] == [(1,)]
    assert len(upstream.writes) == len(upstream.memories) == 1


def test_interruption_after_final_commit_replays_without_http(adapter, monkeypatch):
    service, upstream = adapter
    original = service.journal.record

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        if kwargs.get("complete_request"):
            raise KeyboardInterrupt("synthetic process interruption")

    monkeypatch.setattr(service.journal, "record", interrupted)
    request = AddRequest.model_validate(addition())
    with pytest.raises(KeyboardInterrupt):
        service.add(request)
    assert snapshot(service)[0] == [(1,)]
    service.journal = Journal(service.settings.state_dir)
    call_count = len(upstream.calls)
    service.add(request)
    assert len(upstream.calls) == call_count


def test_old_final_stored_row_completes_without_http(adapter):
    service, upstream = adapter
    request = AddRequest.model_validate(addition())
    key = digest([service.settings.memoria_url, service.owner(request.user_id), request.request_id])
    service.journal.begin(key, digest(request.model_dump()))
    service.journal.record(key, 0, "stored", "synthetic-id", protocol=IDEMPOTENT_PROTOCOL)
    service.add(request)
    assert snapshot(service)[0] == [(1,)]
    assert not upstream.calls


def test_network_calls_do_not_hold_journal_gate(adapter):
    service, upstream = adapter
    other = Journal(service.settings.state_dir, queue_timeout_seconds=0.05)

    def independent_journal_operation(*_):
        other.state("independent-synthetic-request", 0)

    upstream.hook = independent_journal_operation
    service.add(AddRequest.model_validate(addition()))
    assert snapshot(service)[0] == [(1,)]
