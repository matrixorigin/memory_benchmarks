import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.config import Settings
from src.memoria import IDEMPOTENT_PATH


class FakeMemoria:
    """Exercise the real HTTP adapter against Memoria's observed wire contract."""

    def __init__(self):
        self.calls = []
        self.memories = []
        self.hook = None
        self.receipts = {}
        self.receipt_lock = threading.Lock()
        self.active_branch = "main"

    @property
    def writes(self):
        return [
            call
            for call in self.calls
            if call[0] == "POST" and call[1] in {"/v1/memories", IDEMPOTENT_PATH}
        ]

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        owner = request.headers["X-User-Id"]
        assert request.headers["Authorization"] == "Memoria-Owner upstream-secret"
        assert len(owner) == 64 and owner.isascii()
        self.calls.append((request.method, request.url.path, owner, body))
        if self.hook:
            response = self.hook(request, body, owner)
            if response is not None:
                return response
        return self.respond(request, body, owner)

    def respond(self, request, body, owner):
        path = request.url.path
        if request.method == "POST" and path == IDEMPOTENT_PATH:
            key = (owner, body["branch"], body["idempotency_key"])
            with self.receipt_lock:
                if key in self.receipts:
                    original, memory = self.receipts[key]
                    if body != original or not memory["is_active"]:
                        return httpx.Response(422)
                    return httpx.Response(201, json=memory)
                memory = self.make_memory(body, owner)
                identity = ["memoria.store.v1", *key]
                memory["memory_id"] = hashlib.sha256(
                    json.dumps(identity, separators=(",", ":")).encode()
                ).hexdigest()[:32]
                self.receipts[key] = (copy.deepcopy(body), memory)
                self.memories.append(memory)
                return httpx.Response(201, json=memory)
        if request.method == "POST" and path == "/v1/memories":
            memory = self.make_memory(body, owner)
            self.memories.append(memory)
            return httpx.Response(201, json=memory)
        if request.method == "GET":
            memory_id = path.rsplit("/", 1)[1]
            branch = request.url.params.get("branch", self.active_branch)
            memory = next(
                (
                    m
                    for m in self.memories
                    if m["memory_id"] == memory_id
                    and m["user_id"] == owner
                    and m.get("branch", "main") == branch
                ),
                None,
            )
            return httpx.Response(200, content=json.dumps(memory))
        if path == "/v1/memories/query":
            marker = body["extra_metadata_filter"]["aml_write"]
            branch = body.get("branch", self.active_branch)
            items = [
                m
                for m in self.memories
                if m["user_id"] == owner
                and m["extra_metadata"]["aml_write"] == marker
                and m.get("branch", "main") == branch
            ]
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if path == "/v1/memories/search":
            assert "session_id" not in body
            assert "options" not in body
            branch = body.get("branch", self.active_branch)
            items = [
                m
                for m in reversed(self.memories)
                if m["user_id"] == owner and m.get("branch", "main") == branch
            ]
            return httpx.Response(200, json=items)
        raise AssertionError(f"Unexpected upstream path: {path}")

    def make_memory(self, body, owner):
        return {
            **body,
            "memory_id": f"{len(self.memories) + 1:032x}",
            "user_id": owner,
            "is_active": True,
            "retrieval_score": 0.9,
            "created_at": "2026-10-03T12:00:00Z",
        }


@pytest.fixture
def upstream():
    return FakeMemoria()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        state_dir=tmp_path,
    )


@contextmanager
def running(settings, upstream, **client_options):
    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        with TestClient(create_app(settings, client=http), **client_options) as client:
            yield client


def addition(**kwargs):
    return {
        "request_id": "req-1",
        "user_id": "alice",
        "session_id": "session-1",
        "messages": [{"role": "user", "content": "I work on the moon."}],
        **kwargs,
    }


def search(client, **kwargs):
    return client.post(
        "/search",
        json={
            "query": "Where do I work?",
            "user_id": "alice",
            "top_k": 100,
            **kwargs,
        },
    )


def test_round_trip_cross_session_and_user_isolation(settings, upstream):
    with running(settings, upstream) as client:
        first = addition()
        assert client.post("/add", json=first).json() == {
            "success": True,
            "request_id": "req-1",
            "user_id": "alice",
            "session_id": "session-1",
        }
        assert (
            client.post(
                "/add",
                json=addition(
                    request_id="req-2",
                    session_id="session-2",
                    messages=[
                        {"role": "assistant", "content": "Your office is at the lunar base."}
                    ],
                ),
            ).status_code
            == 200
        )
        assert client.post("/add", json=addition(user_id="bob")).status_code == 200
        result = search(client)
        assert result.status_code == 200
        data = result.json()["data"]
        assert [m["content"] for m in data] == [
            "[assistant]\nYour office is at the lunar base.",
            "[user]\nI work on the moon.",
        ]
        assert data[0]["score"] == 0.9
        assert data[0]["created_at"] == "2026-10-03T12:00:00Z"
        assert len(search(client, user_id="bob").json()["data"]) == 1
        assert search(client, user_id="unknown").json() == {"data": []}


def test_ids_preserved_and_long_unicode_owners_distinct(settings, upstream):
    users = ["grp_" + "用户" * 100, "grp_" + "用户" * 100 + " "]
    with running(settings, upstream) as client:
        for user in users:
            payload = addition(user_id=user, request_id=" req ", session_id=" ses ")
            response = client.post("/add", json=payload)
            assert response.status_code == 200
            assert response.json()["user_id"] == user
            assert response.json()["request_id"] == " req "
            assert response.json()["session_id"] == " ses "
        assert upstream.writes[0][2] != upstream.writes[1][2]
        for user in users:
            assert len(search(client, user_id=user).json()["data"]) == 1


def test_idempotency_survives_restart_and_conflicting_payload(settings, upstream):
    payload = addition()
    with running(settings, upstream) as client:
        response = client.post("/add", json=payload)
        assert response.status_code == 200
    with running(settings, upstream) as client:
        assert client.post("/add", json=payload).json() == response.json()
        assert len(upstream.writes) == 1
        for changed in [
            addition(session_id="changed"),
            addition(messages=[{"role": "user", "content": "different"}]),
        ]:
            result = client.post("/add", json=changed)
            assert result.status_code == 400
            assert "restore the original payload" in result.json()["detail"]["reason"]
        assert len(upstream.writes) == 1


@pytest.mark.parametrize("failure", ["timeout", "server_error", "bad_json"])
def test_partial_write_lost_response_replayed_without_duplicates(settings, upstream, failure):
    payload = addition(
        messages=[
            {"role": "user", "content": "First fact"},
            {"role": "assistant", "content": "Second fact"},
        ]
    )

    def hook(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH and len(upstream.writes) == 2:
            upstream.respond(request, body, owner)  # Commit, then lose the acknowledgement.
            if failure == "timeout":
                raise httpx.ReadTimeout("upstream-secret", request=request)
            if failure == "server_error":
                return httpx.Response(500, text="upstream-secret")
            return httpx.Response(201, text="invalid-json")

    upstream.hook = hook
    with running(settings, upstream) as client:
        response = client.post("/add", json=payload)
        assert response.status_code in {502, 504}
        assert "upstream-secret" not in response.text
    upstream.hook = None
    with running(settings, upstream) as client:
        assert client.post("/add", json=payload).status_code == 200
        assert len(upstream.writes) == 3
        assert upstream.writes[1][3] == upstream.writes[2][3]
        assert len(search(client).json()["data"]) == 2
        assert not any(call[1] == "/v1/memories/query" for call in upstream.calls)


def test_ambiguous_uncommitted_atomic_write_recovers(settings, upstream):
    def timeout(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH:
            raise httpx.ReadTimeout("timeout", request=request)

    upstream.hook = timeout
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 504
    upstream.hook = None
    with running(settings, upstream) as client:
        for _ in range(2):
            assert client.post("/add", json=addition()).status_code == 200
        assert len(upstream.writes) == 2
        assert len(upstream.memories) == 1
        assert upstream.writes[0][3] == upstream.writes[1][3]


@pytest.mark.parametrize("failure", ["connection", 400, 403, 422, 425, 429])
def test_definitely_uncommitted_write_can_retry(settings, upstream, failure):
    def hook(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH:
            if failure == "connection":
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(failure, text="upstream-secret")

    upstream.hook = hook
    with running(settings, upstream) as client:
        result = client.post("/add", json=addition())
        assert result.status_code == (503 if failure == "connection" else failure)
        assert "upstream-secret" not in result.text
        upstream.hook = None
        assert client.post("/add", json=addition()).status_code == 200
        assert len(upstream.memories) == 1


def test_add_waits_for_read_visibility_then_retries_without_write(settings, upstream):
    def hidden(request, body, owner):
        if request.method == "GET":
            return httpx.Response(200, content="null")

    upstream.hook = hidden
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 503
        upstream.hook = None
        assert client.post("/add", json=addition()).status_code == 200
        assert len(upstream.writes) == 1


def test_concurrent_requests_across_app_instances(settings, upstream):
    entered = threading.Event()
    release = threading.Event()

    def block(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH:
            entered.set()
            assert release.wait(5)

    upstream.hook = block
    with running(settings, upstream) as first, running(settings, upstream) as second:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(first.post, "/add", json=addition())
            try:
                assert entered.wait(5)
                assert second.post("/add", json=addition()).status_code == 409
            finally:
                release.set()
            assert future.result(timeout=5).status_code == 200
        assert second.post("/add", json=addition()).status_code == 200
        assert len(upstream.writes) == 1


def test_utf8_chunking_timestamp_and_message_order(settings, upstream):
    content = "中文🙂" * 8000
    payload = addition(
        messages=[
            {"role": "user", "content": content, "timestamp": 1704067200123},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}],
                "timestamp": 0,
            },
        ]
    )
    with running(settings, upstream) as client:
        assert client.post("/add", json=payload).status_code == 200
    records = [call[3] for call in upstream.writes]
    assert len(records) > 2
    assert all(len(record["content"].encode()) <= 32768 for record in records)
    assert "".join(record["content"].split("\n", 1)[1] for record in records[:-1]) == content
    assert all(record["observed_at"] == "2024-01-01T00:00:00.123Z" for record in records[:-1])
    assert records[-1]["observed_at"] == "1970-01-01T00:00:00.000Z"
    assert records[-1]["content"].endswith("first\nsecond")
    assert len({record["subject_id"] for record in records}) == len(records)


def test_search_order_top_k_dedup_and_options(settings, upstream):
    with running(settings, upstream) as client:
        for index in range(4):
            assert client.post("/add", json=addition(request_id=str(index))).status_code == 200
        upstream.memories.append(copy.deepcopy(upstream.memories[-1]))
        result = search(client, top_k=2, options=["A. Moon", "B. Earth"])
        assert result.status_code == 200
        assert [m["id"] for m in result.json()["data"]] == [
            upstream.memories[-1]["memory_id"],
            upstream.memories[-3]["memory_id"],
        ]
        assert upstream.calls[-1][3] == {
            "query": "Where do I work?",
            "top_k": 2,
            "branch": "main",
        }
        assert search(client, top_k=1000).status_code == 200
        assert upstream.calls[-1][3]["top_k"] == 100
        assert search(client, query=[{"type": "text", "text": " moon "}]).status_code == 200
        assert upstream.calls[-1][3]["query"] == " moon "


@pytest.mark.parametrize(
    "mutation",
    [
        {"user_id": "another-owner"},
        {"memory_id": ""},
        {"content": ""},
        {"retrieval_score": "high"},
        {"is_active": False},
    ],
)
def test_bad_upstream_evidence_fails_closed(settings, upstream, mutation):
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
        upstream.memories[0].update(mutation)
        upstream.hook = lambda *_: httpx.Response(200, json=upstream.memories)
        result = search(client)
        assert result.status_code in {502, 503}
        assert "data" not in result.json()


@pytest.mark.parametrize("payload", [{"items": []}, None, "invalid"])
def test_invalid_search_envelope(settings, upstream, payload):
    upstream.hook = lambda *_: httpx.Response(200, json=payload)
    with running(settings, upstream) as client:
        assert search(client).status_code == 502


@pytest.mark.parametrize("path", ["/add", "/search"])
@pytest.mark.parametrize(
    "status, expected",
    [
        (202, 502),
        (204, 502),
        (302, 502),
        (400, 400),
        (401, 502),
        (403, 403),
        (404, 502),
        (405, 502),
        (408, 408),
        (409, 409),
        (413, 422),
        (415, 422),
        (422, 422),
        (425, 425),
        (429, 429),
        (500, 502),
        (502, 502),
        (503, 503),
        (504, 504),
        (524, 504),
    ],
)
def test_upstream_errors_preserve_retry_semantics(settings, upstream, path, status, expected):
    upstream.hook = lambda *_: httpx.Response(status, text="upstream-secret")
    with running(settings, upstream) as client:
        result = client.post("/add", json=addition()) if path == "/add" else search(client)
        assert result.status_code == expected
        detail = result.json()["detail"]
        if expected == 422:
            assert detail[0]["loc"] == ["body"]
            assert detail[0]["msg"]
            assert detail[0]["type"] == "value_error"
        else:
            assert isinstance(detail["reason"], str) and detail["reason"]
        assert "upstream-secret" not in result.text


@pytest.mark.parametrize("hint, expected", [("3", "3"), ("120", "60"), ("secret", None)])
def test_rate_limit_delay_is_sanitized(settings, upstream, hint, expected):
    upstream.hook = lambda *_: httpx.Response(
        429, headers={"Retry-After": hint, "X-Secret": "upstream-secret"}
    )
    with running(settings, upstream) as client:
        result = search(client)
        assert result.status_code == 429
        assert result.headers.get("Retry-After") == expected
        assert "X-Secret" not in result.headers
        assert "upstream-secret" not in result.text


def test_visibility_rate_limit_retry_does_not_duplicate_committed_write(settings, upstream):
    def rate_limit_visibility(request, body, owner):
        if request.url.path == "/v1/memories/search":
            return httpx.Response(429)

    upstream.hook = rate_limit_visibility
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 429
        upstream.hook = None
        assert client.post("/add", json=addition()).status_code == 200
        assert len(upstream.writes) == 1


def test_framework_errors_use_business_error_envelope(settings, upstream):
    with running(settings, upstream) as client:
        result = client.get("/add/status/req-1")
        assert result.status_code == 404
        assert "check the URL" in result.json()["detail"]["reason"]
        for path in ["/add", "/search"]:
            result = client.get(path)
            assert result.status_code == 405
            assert "POST" in result.headers["Allow"]
            assert "POST" in result.json()["detail"]["reason"]


@pytest.mark.parametrize("path", ["/add", "/search"])
def test_unexpected_failure_returns_safe_500(settings, upstream, monkeypatch, path):
    def broken(*args):
        raise RuntimeError("database-password-secret")

    monkeypatch.setattr("src.service.MemoryAdapter." + path.lstrip("/"), broken)
    with running(settings, upstream, raise_server_exceptions=False) as client:
        result = client.post("/add", json=addition()) if path == "/add" else search(client)
        assert result.status_code == 500
        assert "Internal adapter error" in result.json()["detail"]["reason"]
        assert "database-password-secret" not in result.text


def test_lock_storage_failure_is_retryable(settings, upstream):
    with running(settings, upstream) as client:
        locks = settings.state_dir / "locks"
        unavailable = settings.state_dir / "unavailable"
        locks.rename(unavailable)
        result = client.post("/add", json=addition())
        assert result.status_code == 503
        assert "state storage" in result.json()["detail"]["reason"]
        assert not upstream.calls
        unavailable.rename(locks)
        assert client.post("/add", json=addition()).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer client-secret"},
        {"Authorization": "Token client-secret"},
        {"X-Api-Key": "client-secret"},
    ],
)
def test_authentication_supported_schemes(settings, upstream, headers):
    from dataclasses import replace

    with running(replace(settings, api_key="client-secret"), upstream) as client:
        assert client.post("/add", json=addition(), headers=headers).status_code == 200
        assert (
            client.post(
                "/search", json={"query": "moon", "user_id": "alice", "top_k": 1}, headers=headers
            ).status_code
            == 200
        )


def test_authentication_rejects_missing_or_wrong_key(settings, upstream):
    from dataclasses import replace

    with running(replace(settings, api_key="client-secret"), upstream) as client:
        for path, payload in [
            ("/add", addition()),
            ("/search", {"query": "moon", "user_id": "alice", "top_k": 1}),
        ]:
            for headers in [{}, {"Authorization": "Bearer wrong"}]:
                result = client.post(path, json=payload, headers=headers)
                assert result.status_code == 401
                assert result.json() == {"detail": {"reason": "Invalid or missing adapter API key"}}
        assert not upstream.calls


@pytest.mark.parametrize(
    "change",
    [
        {"request_id": " "},
        {"user_id": 1},
        {"session_id": None},
        {"messages": []},
        {"messages": [{"role": "system", "content": "secret content"}]},
        {"messages": [{"role": "user", "content": " "}]},
        {"messages": [{"role": "user", "content": "ok", "timestamp": True}]},
        {"messages": [{"role": "user", "content": "ok", "timestamp": 10**30}]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abcd"}}
                    ],
                }
            ]
        },
    ],
)
def test_add_validation(settings, upstream, change):
    with running(settings, upstream) as client:
        response = client.post("/add", json=addition(**change))
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
        assert all(set(issue) == {"loc", "msg", "type"} for issue in response.json()["detail"])
        assert "secret content" not in response.text
        assert not upstream.calls


@pytest.mark.parametrize(
    "change",
    [
        {"query": ""},
        {"query": []},
        {"user_id": ""},
        {"top_k": 0},
        {"top_k": -1},
        {"top_k": True},
        {"top_k": "10"},
        {"options": "A"},
        {"session_id": "ignored?"},
    ],
)
def test_search_validation(settings, upstream, change):
    with running(settings, upstream) as client:
        assert search(client, **change).status_code == 422
        assert not upstream.calls


def test_missing_fields_and_invalid_json(settings, upstream):
    with running(settings, upstream) as client:
        for field in ("request_id", "user_id", "session_id", "messages"):
            payload = addition()
            del payload[field]
            assert client.post("/add", json=payload).status_code == 422
        assert client.post("/search", json={"query": "moon", "user_id": "alice"}).status_code == 422
        assert (
            client.post(
                "/add", content="{", headers={"Content-Type": "application/json"}
            ).status_code
            == 422
        )
        assert not upstream.calls


def test_health_is_unauthenticated_with_protected_endpoints(settings, upstream):
    from dataclasses import replace

    with running(replace(settings, api_key="client-secret"), upstream) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        assert client.post("/add", json=addition()).status_code == 401
        assert search(client).status_code == 401
        assert not upstream.calls


def test_add_requires_search_visibility_not_just_point_read(settings, upstream):
    def delayed_index(request, body, owner):
        if request.url.path == "/v1/memories/search":
            return httpx.Response(200, json=[])

    upstream.hook = delayed_index
    with running(settings, upstream) as client:
        response = client.post("/add", json=addition())
        assert response.status_code == 503
        assert "not yet searchable" in response.text
        assert len(upstream.memories) == 1
        upstream.hook = None
        assert client.post("/add", json=addition()).status_code == 200
        assert len(upstream.writes) == 1
