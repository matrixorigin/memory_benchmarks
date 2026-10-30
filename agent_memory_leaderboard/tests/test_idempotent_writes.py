"""Synthetic recovery and upgrade tests; no official evaluation payloads."""

import multiprocessing
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from test_endpoints import FakeMemoria, addition, running

from src.config import Settings
from src.errors import AdapterError
from src.journal import IDEMPOTENT_PROTOCOL, LEGACY_PROTOCOL, TOKEN_PROTOCOL, Journal
from src.memoria import IDEMPOTENT_PATH, Memoria
from src.models import AddRequest
from src.service import MemoryAdapter, digest


@pytest.fixture
def settings(tmp_path):
    return Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        state_dir=tmp_path,
    )


@pytest.fixture
def upstream():
    return FakeMemoria()


def legacy_journal(settings, upstream, payload, entries, *, complete=False):
    """Seed the actual pre-upgrade four-column schema and optional committed rows."""
    request = AddRequest.model_validate(payload)
    owner = digest([settings.namespace, request.user_id])
    key = digest([settings.memoria_url, owner, request.request_id])
    bodies = list(
        MemoryAdapter(settings, None, None).write_bodies(request, key, policy={"version": 1})
    )
    with sqlite3.connect(settings.state_dir / "requests.sqlite3") as db:
        db.executescript("""
            CREATE TABLE requests(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                                  complete INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE writes(request_key TEXT NOT NULL, position INTEGER NOT NULL,
                                state TEXT NOT NULL, memory_id TEXT,
                                PRIMARY KEY(request_key, position));
        """)
        db.execute(
            "INSERT INTO requests VALUES (?, ?, ?)",
            (key, digest(request.model_dump()), int(complete)),
        )
        for position, (state, committed, known_id) in enumerate(entries):
            memory_id = None
            if committed:
                memory = upstream.make_memory(bodies[position], owner)
                upstream.memories.append(memory)
                if known_id:
                    memory_id = memory["memory_id"]
            db.execute("INSERT INTO writes VALUES (?, ?, ?, ?)", (key, position, state, memory_id))
    return key


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_precommit_server_error_recovers_after_restart(settings, upstream, status):
    upstream.hook = lambda request, *_: (
        httpx.Response(status) if request.url.path == IDEMPOTENT_PATH else None
    )
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code in {502, 503, 504}
    assert not upstream.memories
    upstream.hook = None
    with running(settings, upstream) as client:
        for _ in range(3):
            assert client.post("/add", json=addition()).status_code == 200
    assert len(upstream.writes) == 2
    assert upstream.writes[0][3] == upstream.writes[1][3]
    assert len(upstream.memories) == 1


def _exit_after_inflight(directory):
    """Terminate without unwinding Python contexts after the SQLite commit."""
    settings = Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        state_dir=directory,
    )
    journal = Journal(directory)
    prepare = journal.prepare_write

    def interrupt(*args, **kwargs):
        prepare(*args, **kwargs)
        os._exit(23)

    def unexpected_post(request):
        (directory / "unexpected-post").touch()
        raise AssertionError("Process should exit before HTTP")

    journal.prepare_write = interrupt
    with httpx.Client(transport=httpx.MockTransport(unexpected_post)) as http:
        adapter = MemoryAdapter(settings, Memoria(settings, http), journal)
        adapter.add(AddRequest.model_validate(addition()))


def test_process_exit_after_inflight_before_http_recovers(settings, upstream):
    process = multiprocessing.get_context("spawn").Process(
        target=_exit_after_inflight,
        args=(settings.state_dir,),
    )
    process.start()
    try:
        process.join(10)
        assert process.exitcode == 23
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert not (settings.state_dir / "unexpected-post").exists()
    with sqlite3.connect(settings.state_dir / "requests.sqlite3") as db:
        assert db.execute("SELECT state, protocol FROM writes").fetchall() == [
            ("inflight", TOKEN_PROTOCOL)
        ]
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
    assert len(upstream.writes) == len(upstream.memories) == 1


@pytest.mark.parametrize("old_commit_first", [True, False])
def test_late_original_write_and_retry_share_one_receipt(settings, upstream, old_commit_first):
    original = []

    def delayed(request, body, owner):
        if request.url.path != IDEMPOTENT_PATH:
            return None
        if not original:
            original.append((request, body, owner))
            raise httpx.ReadTimeout("synthetic response loss", request=request)
        if old_commit_first:
            upstream.respond(*original[0])
        response = upstream.respond(request, body, owner)
        if not old_commit_first:
            upstream.respond(*original[0])
        return response

    upstream.hook = delayed
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 504
        assert client.post("/add", json=addition()).status_code == 200
    assert len(upstream.writes) == 2
    assert len(upstream.memories) == 1


def test_concurrent_replays_with_independent_journals_share_receipt(settings, upstream):
    barrier = threading.Barrier(2)

    def overlap(request, *_):
        if request.url.path == IDEMPOTENT_PATH:
            barrier.wait(timeout=5)

    upstream.hook = overlap
    other = replace(settings, state_dir=settings.state_dir / "second-journal")
    with running(settings, upstream) as first, running(other, upstream) as second:
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [
                pool.submit(client.post, "/add", json=addition()) for client in (first, second)
            ]
            assert [call.result(timeout=10).status_code for call in calls] == [200, 200]
    assert len(upstream.writes) == 2
    assert len(upstream.memories) == 1


@pytest.mark.parametrize("status", [404, 405])
def test_old_backend_never_falls_back_to_ordinary_post(settings, upstream, status):
    upstream.hook = lambda request, *_: (
        httpx.Response(status) if request.url.path == IDEMPOTENT_PATH else None
    )
    with running(settings, upstream) as client:
        for _ in range(2):
            response = client.post("/add", json=addition())
            assert response.status_code == 502
            assert "atomic writes unsupported" in response.json()["detail"]["reason"]
    assert not upstream.memories
    assert all(call[1] == IDEMPOTENT_PATH for call in upstream.writes)
    upstream.hook = None
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
    assert len(upstream.memories) == 1


def test_legacy_unknown_write_stays_legacy_without_post(settings, upstream):
    key = legacy_journal(settings, upstream, addition(), [("inflight", False, False)])
    for _ in range(2):
        with running(settings, upstream) as client:
            response = client.post("/add", json=addition())
            assert response.status_code == 503
            assert "outcome is unknown" in response.text
    assert not upstream.writes
    assert Journal(settings.state_dir).state(key, 0) == ("inflight", None, LEGACY_PROTOCOL)


@pytest.mark.parametrize("known_id", [False, True])
def test_committed_legacy_write_recovers_without_atomic_receipt(settings, upstream, known_id):
    key = legacy_journal(settings, upstream, addition(), [("inflight", True, known_id)])
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
    assert not upstream.writes
    assert not upstream.receipts
    assert len(upstream.memories) == 1
    state, _, protocol = Journal(settings.state_dir).state(key, 0)
    assert (state, protocol) == ("stored", LEGACY_PROTOCOL)
    queries = [call for call in upstream.calls if call[1] == "/v1/memories/query"]
    assert bool(queries) is not known_id


def test_legacy_pending_write_can_use_atomic_route(settings, upstream):
    key = legacy_journal(settings, upstream, addition(), [("pending", False, False)])
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
    state, _, protocol = Journal(settings.state_dir).state(key, 0)
    assert (state, protocol) == ("stored", IDEMPOTENT_PROTOCOL)
    assert len(upstream.memories) == len(upstream.receipts) == 1


def test_partial_legacy_request_preserves_old_chunks_and_writes_new_chunks(settings, upstream):
    payload = addition(
        messages=[{"role": "user", "content": f"Synthetic fact {i}"} for i in range(3)]
    )
    key = legacy_journal(
        settings, upstream, payload, [("stored", True, True), ("inflight", True, False)]
    )
    with running(settings, upstream) as client:
        assert client.post("/add", json=payload).status_code == 200
        assert client.post("/add", json=payload).status_code == 200
    assert len(upstream.writes) == 1
    assert len(upstream.memories) == 3
    journal = Journal(settings.state_dir)
    assert [journal.state(key, i)[2] for i in range(3)] == [
        LEGACY_PROTOCOL,
        LEGACY_PROTOCOL,
        IDEMPOTENT_PROTOCOL,
    ]


def test_completed_legacy_request_replays_offline(settings, upstream):
    legacy_journal(settings, upstream, addition(), [("stored", True, True)], complete=True)
    upstream.hook = lambda *_: httpx.Response(503)
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
    assert not upstream.calls


def test_unknown_journal_protocol_fails_before_any_backend_call(settings, upstream):
    key = legacy_journal(settings, upstream, addition(), [("inflight", False, False)])
    journal = Journal(settings.state_dir)
    journal.record(key, 0, "inflight", protocol="future_protocol")
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 503
    assert not upstream.calls


def test_old_binary_four_column_writes_fail_after_schema_upgrade(settings, upstream):
    key = legacy_journal(settings, upstream, addition(), [("inflight", False, False)])
    Journal(settings.state_dir)
    with sqlite3.connect(settings.state_dir / "requests.sqlite3") as db:
        with pytest.raises(sqlite3.OperationalError):
            # Exact old record() statement: downgrade cannot relabel an atomic
            # attempt as legacy and then blindly send a non-idempotent POST.
            db.execute(
                "INSERT INTO writes VALUES (?, ?, ?, ?) "
                "ON CONFLICT(request_key, position) DO UPDATE SET "
                "state = excluded.state, memory_id = excluded.memory_id",
                (key, 0, "inflight", None),
            )


def test_atomic_operations_pin_branch_instead_of_following_checkout(settings, upstream):
    def require_branch(request, body, owner):
        if request.method == "GET":
            assert request.url.params["branch"] == "main"
        elif request.url.path in {IDEMPOTENT_PATH, "/v1/memories/search"}:
            assert body["branch"] == "main"

    upstream.hook = require_branch
    upstream.active_branch = "other"
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
        assert (
            client.post(
                "/search", json={"user_id": "alice", "query": "fact", "top_k": 1}
            ).status_code
            == 200
        )


def test_legacy_write_outside_main_is_not_reposted_into_main(settings, upstream):
    legacy_journal(settings, upstream, addition(), [("inflight", True, False)])
    upstream.memories[0]["branch"] = "other"
    upstream.active_branch = "other"
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 503
    assert not upstream.writes
    assert len(upstream.memories) == 1


def test_backend_rejects_changed_body_for_same_atomic_key(settings, upstream):
    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        memoria = Memoria(settings, http)
        request = AddRequest.model_validate(addition())
        adapter = MemoryAdapter(settings, memoria, None)
        owner = adapter.owner(request.user_id)
        body = next(adapter.write_bodies(request, "synthetic-key", policy={"version": 1}))
        marker = body["extra_metadata"]["aml_write"]
        memoria.store_idempotent(owner, body, marker)
        with pytest.raises(AdapterError) as raised:
            memoria.store_idempotent(owner, {**body, "content": "Changed synthetic fact"}, marker)
        assert raised.value.status == 422
    assert len(upstream.memories) == 1


def test_inactive_receipt_is_not_resurrected_on_replay(settings, upstream):
    def lost_response(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH:
            upstream.respond(request, body, owner)
            upstream.memories[0]["is_active"] = False
            raise httpx.ReadTimeout("synthetic response loss", request=request)

    upstream.hook = lost_response
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 504
    upstream.hook = None
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 422
    assert len(upstream.memories) == 1
    assert not upstream.memories[0]["is_active"]
