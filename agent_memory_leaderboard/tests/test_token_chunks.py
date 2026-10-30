"""Real pinned tokenizer, synthetic inputs only; no test-time network access."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from test_endpoints import FakeMemoria, addition

from src.config import Settings
from src.embedding_tokens import DEFAULT_TOKEN_BUDGET, TOKENIZER_SHA256, token_count
from src.errors import AdapterError
from src.ingestion import MAX_MEMORY_BYTES, message_parts
from src.journal import Journal
from src.memoria import Memoria
from src.models import AddRequest, Message
from src.service import MemoryAdapter, digest


def dense_text():
    return "Synthetic event identifiers: " + " ".join(
        hashlib.sha256(f"synthetic-event-{i}".encode()).hexdigest()[:32] for i in range(900)
    )


def test_reported_byte_valid_token_overflow_is_split_without_loss():
    message = Message(role="user", content=dense_text(), timestamp=1704067200000)
    legacy = list(message_parts(message, token_budget=None))
    assert len(legacy) == 1
    assert len(legacy[0][0].encode()) < MAX_MEMORY_BYTES
    assert token_count(legacy[0][0]) > 8192
    parts = list(message_parts(message))
    assert len(parts) > 1
    header = "[user at 2024-01-01T00:00:00.000Z]\n"
    assert "".join(p.removeprefix(header) for p, _ in parts) == message.content
    assert all(token_count(p) <= DEFAULT_TOKEN_BUDGET for p, _ in parts)
    assert all(len(p.encode()) <= MAX_MEMORY_BYTES for p, _ in parts)
    assert {t for _, t in parts} == {"2024-01-01T00:00:00.000Z"}


@pytest.mark.parametrize(
    "text",
    [
        "  spaces\n\n",
        "e\u0301é中文🙂𐐀\n",
        "<s> special </s> ",
        "a" * 333,
    ],
)
def test_original_text_preserved_even_when_tokenizer_normalizes(text):
    message = Message(role="assistant", content=text * 200)
    header = "[assistant]\n"
    parts = list(message_parts(message, token_budget=128))
    assert "".join(p[len(header) :] for p, _ in parts) == message.content
    assert all(token_count(p) <= 128 and len(p.encode()) <= MAX_MEMORY_BYTES for p, _ in parts)


def test_short_message_unchanged_and_concurrent_tokenization_deterministic():
    message = Message(role="user", content="A short fact.", timestamp=-1)
    assert list(message_parts(message)) == list(message_parts(message, token_budget=None))
    large = Message(role="assistant", content=dense_text())
    with ThreadPoolExecutor(max_workers=16) as executor:
        outputs = list(executor.map(lambda _: list(message_parts(large)), range(16)))
    assert all(result == outputs[0] for result in outputs)


@pytest.fixture
def adapter(tmp_path):
    settings = Settings(
        memoria_url="http://memoria.test", memoria_master_key="upstream-secret", state_dir=tmp_path
    )
    upstream = FakeMemoria()
    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        yield MemoryAdapter(settings, Memoria(settings, http), Journal(tmp_path)), upstream


def request_key(service, request):
    return digest(
        [service.settings.memoria_url, service.owner(request.user_id), request.request_id]
    )


def test_partial_retry_after_restart_keeps_persisted_budget_and_exact_bodies(adapter, monkeypatch):
    service, upstream = adapter
    request = AddRequest.model_validate(
        addition(messages=[{"role": "user", "content": dense_text()}])
    )
    original = service.write
    calls = 0

    def crash(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic interruption before second POST")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "write", crash)
    with pytest.raises(RuntimeError):
        service.add(request)
    assert len(upstream.memories) == 1
    with sqlite3.connect(service.journal.path) as db:
        policy = json.loads(db.execute("SELECT render_policy FROM requests").fetchone()[0])
    assert policy == {"version": 2, "tokenizer_sha256": TOKENIZER_SHA256, "max_tokens": 7680}
    expected = list(service.write_bodies(request, request_key(service, request), policy))
    changed = replace(service.settings, embedding_token_budget=256)
    restarted = MemoryAdapter(changed, service.memoria, Journal(changed.state_dir))
    restarted.add(request)
    assert len(upstream.memories) == len(expected)
    assert sorted(m["content"] for m in upstream.memories) == sorted(b["content"] for b in expected)
    before = len(upstream.calls)
    restarted.add(request)
    assert len(upstream.calls) == before
    # A new request uses the changed setting, rather than inheriting another request's policy.
    newer = request.model_copy(update={"request_id": "another-request"})
    restarted.add(newer)
    with sqlite3.connect(service.journal.path) as db:
        row = db.execute(
            "SELECT render_policy FROM requests WHERE key=?", (request_key(service, newer),)
        ).fetchone()
    assert json.loads(row[0])["max_tokens"] == 256


def test_migrate_old_database_pins_v1_and_completed_replay_skips_rendering(tmp_path, monkeypatch):
    request = AddRequest.model_validate(addition())
    settings = Settings(
        memoria_url="http://memoria.test", memoria_master_key="upstream-secret", state_dir=tmp_path
    )
    key = digest(
        [settings.memoria_url, digest([settings.namespace, request.user_id]), request.request_id]
    )
    with sqlite3.connect(tmp_path / "requests.sqlite3") as db:
        db.execute(
            "CREATE TABLE requests(key TEXT PRIMARY KEY, fingerprint TEXT, complete INTEGER)"
        )
        db.execute("INSERT INTO requests VALUES(?,?,1)", (key, digest(request.model_dump())))
    journal = Journal(tmp_path)
    with sqlite3.connect(journal.path) as db:
        assert json.loads(db.execute("SELECT render_policy FROM requests").fetchone()[0]) == {
            "version": 1
        }
    service = MemoryAdapter(settings, None, journal)
    monkeypatch.setattr(
        service, "write_bodies", lambda *_: pytest.fail("completed replay rendered")
    )
    assert service.add(request).success
    with pytest.raises(AdapterError) as exc:
        service.add(request.model_copy(update={"session_id": "changed"}))
    assert exc.value.status == 400


def test_incomplete_old_request_retains_byte_boundaries(adapter):
    service, _ = adapter
    request = AddRequest.model_validate(
        addition(messages=[{"role": "user", "content": dense_text()}])
    )
    key = request_key(service, request)
    service.journal.begin(key, digest(request.model_dump()))
    policy = service.journal.request_policy(key, digest(request.model_dump()), {"version": 2})
    assert policy == {"version": 1}
    bodies = list(service.write_bodies(request, key, policy))
    assert len(bodies) == 1 and token_count(bodies[0]["content"]) > 8192


@pytest.mark.parametrize(
    "policy",
    [
        {"version": 3},
        {"version": True},
        {"version": 2, "max_tokens": 1},
        {"version": 2, "max_tokens": 7680, "tokenizer_sha256": "different"},
    ],
)
def test_unknown_policy_fails_before_backend_write(adapter, policy):
    service, upstream = adapter
    request = AddRequest.model_validate(addition())
    key = request_key(service, request)
    service.journal.begin(key, digest(request.model_dump()))
    with sqlite3.connect(service.journal.path) as db:
        db.execute("UPDATE requests SET render_policy=?", (json.dumps(policy),))
    with pytest.raises(AdapterError) as exc:
        service.add(request)
    assert exc.value.status == 503 and not upstream.calls


@pytest.mark.parametrize("budget", [0, 127, 8193, True, 7680.0])
def test_invalid_token_budget_rejected(budget):
    with pytest.raises(ValueError):
        Settings(
            memoria_url="http://memoria.test",
            memoria_master_key="upstream-secret",
            embedding_token_budget=budget,
        )


def test_add_and_visibility_queries_obey_real_token_budget(adapter):
    service, upstream = adapter
    checked = {"store": 0, "visibility": 0}

    def reject_oversize(request, body, _owner):
        if request.url.path == "/v1/memories/idempotent":
            checked["store"] += 1
            text = body["content"]
        elif request.url.path == "/v1/memories/search":
            checked["visibility"] += 1
            text = body["query"]
        else:
            return None
        if token_count(text) > DEFAULT_TOKEN_BUDGET:
            return httpx.Response(500)
        return None

    upstream.hook = reject_oversize
    request = AddRequest.model_validate(
        addition(messages=[{"role": "user", "content": dense_text()}])
    )
    assert service.add(request).success
    assert checked["store"] == checked["visibility"] > 1
    assert "".join(m["content"].removeprefix("[user]\n") for m in upstream.memories) == dense_text()


def test_tokenizer_asset_missing_or_modified_fails_closed(tmp_path, monkeypatch):
    from src import embedding_tokens

    embedding_tokens.load_tokenizer.cache_clear()
    monkeypatch.setenv("ADAPTER_TOKENIZER_PATH", str(tmp_path / "tokenizer.json"))
    try:
        with pytest.raises(RuntimeError, match="missing"):
            embedding_tokens.load_tokenizer()
        (tmp_path / "tokenizer.json").write_text("{}")
        with pytest.raises(RuntimeError, match="checksum"):
            embedding_tokens.load_tokenizer()
    finally:
        embedding_tokens.load_tokenizer.cache_clear()
