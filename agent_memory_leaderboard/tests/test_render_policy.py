"""Synthetic regressions for attribution and downgrade safety."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from test_endpoints import FakeMemoria, addition, running

from src import benchmark
from src.config import Settings
from src.embedding_tokens import token_policy
from src.errors import AdapterError
from src.evidence import SourceManifest, source_messages
from src.ingestion import message_parts
from src.journal import IDEMPOTENT_PROTOCOL, TOKEN_PROTOCOL, Journal
from src.models import AddRequest, Message
from src.service import MemoryAdapter, digest


def dense():
    return " ".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(500))


def conversation():
    return {
        "session_1_date_time": "2025-01-01",
        "session_1": [{"dia_id": "D1:1", "speaker": "A", "text": dense()}],
        "session_2_date_time": "2025-01-02",
        "session_2": [{"dia_id": "D2:1", "speaker": "A", "text": dense()}],
    }


def test_manifest_uses_explicit_mixed_policies_and_reader_metadata():
    data = conversation()
    policies = [{"version": 1}, token_policy(4096)]
    manifest = SourceManifest(data, policy=policies)
    for (_, _, source, body), policy in zip(source_messages(data), policies, strict=True):
        parts = [
            p
            for p, _ in message_parts(
                Message.model_validate(body), token_budget=policy.get("max_tokens")
            )
        ]
        mapped, audit = manifest.audit(parts)
        assert all(ids == [source] for ids in mapped)
        assert audit["unmapped_records"] == 0
        assert all(
            row is not None and row["date"] != "unknown" for row in manifest.reader_records(parts)
        )
    assert manifest.dump()["render_policies"] == policies
    assert manifest.dump()["version"] == 2
    with pytest.raises(ValueError, match="count mismatch"):
        SourceManifest(data, policy=[token_policy(4096)])
    with pytest.raises(TypeError):
        SourceManifest(data)
    with pytest.raises(TypeError):
        list(message_parts(Message(role="user", content="fact")))


def test_policy_endpoint_auth_fingerprint_persistence_and_no_backend_calls(tmp_path):
    settings = Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        state_dir=tmp_path,
        api_key="synthetic-key",
        embedding_token_budget=4096,
    )
    upstream = FakeMemoria()
    payload = addition()
    auth = {"Authorization": "Bearer synthetic-key"}
    with running(settings, upstream) as client:
        assert client.post("/diagnostics/render-policy", json=payload).status_code == 401
        assert (
            client.post("/diagnostics/render-policy", json=payload, headers=auth).status_code == 503
        )
        assert client.post("/add", json=payload, headers=auth).status_code == 200
    before = len(upstream.calls)
    with running(replace(settings, embedding_token_budget=256), upstream) as client:
        result = client.post("/diagnostics/render-policy", json=payload, headers=auth)
        assert result.status_code == 200 and result.json() == {"policy": token_policy(4096)}
        assert (
            client.post(
                "/diagnostics/render-policy",
                json={**payload, "session_id": "changed"},
                headers=auth,
            ).status_code
            == 400
        )
    assert len(upstream.calls) == before


@pytest.mark.parametrize("state", ["inflight", "stored"])
def test_old_protocol_reader_rejects_existing_token_positions(tmp_path, state):
    journal = Journal(tmp_path)
    journal.prepare_write("key", "fingerprint", 0, render_policy=token_policy(7680))
    journal.record("key", 0, state, "synthetic-memory", protocol=TOKEN_PROTOCOL)
    # The old binary checks this whitelist before its stored/complete fast path.
    with journal.connect("prepare_write") as db:
        _, _, protocol = db.execute(
            "SELECT state, memory_id, protocol FROM writes WHERE request_key=? AND position=?",
            ("key", 0),
        ).fetchone()
        assert protocol not in {"legacy", IDEMPOTENT_PROTOCOL}
        assert db.execute("SELECT complete FROM requests").fetchone()[0] == 0


@pytest.mark.parametrize("position", [0, 1, 7])
def test_sql_guard_blocks_old_protocol_including_unwritten_positions(tmp_path, position):
    journal = Journal(tmp_path)
    journal.prepare_write("key", "fingerprint", 0, render_policy=token_policy(7680))
    # This is the old binary's actual UPSERT helper; SQLite rejects both insertion
    # of missing positions and replacement of already-recorded token receipts.
    with pytest.raises(AdapterError) as error:
        journal.record("key", position, "inflight", protocol=IDEMPOTENT_PROTOCOL)
    assert error.value.status == 503
    assert journal.state("key", 0)[2] == TOKEN_PROTOCOL
    if position:
        assert journal.state("key", position)[0] == "pending"
    # Current code can continue, and old v1 requests remain writable.
    journal.prepare_write("key", "fingerprint", 1, render_policy=token_policy(7680))
    assert journal.state("key", 1)[2] == TOKEN_PROTOCOL
    journal.prepare_write("old-key", "old-fingerprint", 0, render_policy={"version": 1})
    assert journal.state("old-key", 0)[2] == IDEMPOTENT_PROTOCOL


def test_runner_mixed_old_and_custom_budget_attribution_and_resume(tmp_path, monkeypatch):
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps(
            [
                {
                    "sample_id": "sample",
                    "conversation": conversation(),
                    "qa": [
                        {
                            "question": "identifiers",
                            "category": 1,
                            "answer": "synthetic",
                            "evidence": ["D1:1", "D2:1"],
                        }
                    ],
                }
            ]
        )
    )
    settings = Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        state_dir=tmp_path / "state",
        embedding_token_budget=4096,
    )
    upstream = FakeMemoria()
    args = SimpleNamespace(
        dataset=source,
        kind="locomo",
        conversations=1,
        top_k=100,
        adapter_url="http://adapter.test",
        output=tmp_path / "run.json",
        resume=False,
    )
    original_client = httpx.Client
    policy_calls = []
    with running(settings, upstream) as client:

        def route(request):
            body = json.loads(request.content) if request.content else {}
            if request.url.path == "/add" and body["session_id"] == "session_1":
                adapter = MemoryAdapter(settings, None, Journal(settings.state_dir))
                key = digest(
                    [settings.memoria_url, adapter.owner(body["user_id"]), body["request_id"]]
                )
                adapter.journal.begin(key, digest(AddRequest.model_validate(body).model_dump()))
            if request.url.path == "/diagnostics/render-policy":
                policy_calls.append(body["request_id"])
            response = client.request(
                request.method,
                request.url.path,
                content=request.content,
                headers={"Content-Type": "application/json"},
            )
            return httpx.Response(response.status_code, content=response.content)

        monkeypatch.setattr(
            benchmark.httpx,
            "Client",
            lambda **kw: original_client(transport=httpx.MockTransport(route), **kw),
        )
        assert benchmark.run(args) == 0
        report = json.loads(args.output.read_text())
        question = next(iter(report["questions"].values()))
        assert question["evidence_recall"] == 1
        assert question["source_attribution"]["unmapped_records"] == 0
        manifest = json.loads(next((tmp_path / "run.sources").glob("*.json")).read_text())
        assert manifest["render_policies"] == [{"version": 1}, token_policy(4096)]
        before = len(upstream.writes)
        args.resume = True
        assert benchmark.run(args) == 0
        assert len(upstream.writes) == before and len(policy_calls) == 4


@pytest.mark.parametrize(
    "legacy,status,policy,success",
    [
        (False, 404, None, False),
        (True, 404, None, True),
        (False, 200, {"version": 99}, False),
        (True, 200, token_policy(4096), False),
        (True, 403, None, False),
    ],
)
def test_runner_missing_policy_fails_closed_unless_explicit_old_adapter(
    tmp_path,
    monkeypatch,
    legacy,
    status,
    policy,
    success,
):
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps([{"sample_id": "sample", "conversation": conversation(), "qa": []}])
    )

    def route(request):
        if request.url.path == "/diagnostics/render-policy":
            return httpx.Response(status, json={"policy": policy})
        if request.url.path == "/add":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    **{k: body[k] for k in ("request_id", "user_id", "session_id")},
                },
            )
        return httpx.Response(200, json={"status": "ok"})

    original = httpx.Client
    monkeypatch.setattr(
        benchmark.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(route), **kw)
    )
    args = SimpleNamespace(
        dataset=source,
        kind="locomo",
        conversations=1,
        top_k=100,
        adapter_url="http://adapter.test",
        output=tmp_path / "run.json",
        resume=False,
        legacy_byte_rendering=legacy,
    )
    assert benchmark.run(args) == (0 if success else 1)
