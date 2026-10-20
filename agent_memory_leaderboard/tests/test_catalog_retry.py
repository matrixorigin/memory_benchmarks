"""Synthetic catalog-lag faults; no environment or evaluation payloads."""

from dataclasses import replace

import httpx
import pytest

from src import budget as budget_module
from src import memoria as memoria_module
from src import scheduling as scheduling_module
from src.budget import request_budget
from src.errors import AdapterError
from src.journal import IDEMPOTENT_PROTOCOL, Journal
from src.memoria import IDEMPOTENT_PATH, Memoria
from src.models import AddRequest
from src.service import digest
from tests.test_endpoints import addition, running
from tests.test_endpoints import settings as settings
from tests.test_endpoints import upstream as upstream
from tests.test_write_visibility import Clock

PARSER_MISSING = (
    'Database error: error returned from database: 1064 (42000): '
    'SQL parser error: table "mem_memories" does not exist'
)
EXECUTOR_MISSING = (
    "Database error: error returned from database: 1146 (42S02): "
    "no such table synthetic_db.mem_memories"
)


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(memoria_module, "time", value)
    monkeypatch.setattr(budget_module, "time", value)
    monkeypatch.setattr(scheduling_module, "time", value)
    return value


@pytest.mark.parametrize("error", [PARSER_MISSING, EXECUTOR_MISSING])
@pytest.mark.parametrize("committed", [False, True])
def test_atomic_write_retries_identical_payload_without_duplicates(
    settings, upstream, clock, error, committed, caplog
):
    def fail_once(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH and len(upstream.writes) == 1:
            if committed:
                upstream.respond(request, body, owner)
            return httpx.Response(500, text=error + " synthetic-sensitive-diagnostic")

    upstream.hook = fail_once
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
        assert client.post("/add", json=addition()).status_code == 200
    assert clock.sleeps == [0.5]
    assert len(upstream.writes) == 2
    assert upstream.writes[0] == upstream.writes[1]
    assert len(upstream.memories) == len(upstream.receipts) == 1
    assert "catalog visibility retry" in caplog.text
    assert "synthetic-sensitive-diagnostic" not in caplog.text
    assert "synthetic_db" not in caplog.text
    assert settings.memoria_master_key not in caplog.text
    assert upstream.writes[0][3]["idempotency_key"] not in caplog.text


@pytest.mark.parametrize("stage", ["read", "search"])
def test_add_visibility_missing_table_retries_without_reposting(
    settings, upstream, clock, stage
):
    attempts = []

    def lag(request, body, owner):
        selected = request.method == "GET" if stage == "read" else (
            request.url.path == "/v1/memories/search"
        )
        if selected:
            attempts.append((str(request.url), body))
            if len(attempts) == 1:
                return httpx.Response(500, text=PARSER_MISSING)

    upstream.hook = lag
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(upstream.writes) == len(upstream.memories) == 1
    assert clock.sleeps == [0.5]


def test_search_endpoint_recovers_catalog_error(settings, upstream, clock):
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
        calls = []

        def lag(request, body, owner):
            if request.url.path == "/v1/memories/search":
                calls.append(body)
                if len(calls) == 1:
                    return httpx.Response(500, text=EXECUTOR_MISSING)

        upstream.hook = lag
        response = client.post(
            "/search", json={"user_id": "alice", "query": "moon", "top_k": 100}
        )
    assert response.status_code == 200 and len(response.json()["data"]) == 1
    assert len(calls) == 2 and calls[0] == calls[1]
    assert len(upstream.writes) == 1


def test_retry_exhaustion_preserves_atomic_intent_and_later_replay(
    settings, upstream, clock
):
    def unavailable(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH:
            upstream.respond(request, body, owner)
            return httpx.Response(500, text=EXECUTOR_MISSING)

    upstream.hook = unavailable
    model = AddRequest.model_validate(addition())
    with running(settings, upstream) as client:
        response = client.post("/add", json=addition())
        assert response.status_code == 502
        assert "synthetic_db" not in response.text
        assert "mem_memories" not in response.text
        owner = digest([settings.namespace, model.user_id])
        key = digest([settings.memoria_url, owner, model.request_id])
        assert Journal(settings.state_dir).state(key, 0) == (
            "inflight", None, IDEMPOTENT_PROTOCOL
        )
        assert len(upstream.writes) == 6
        assert sum(clock.sleeps) == 5
        assert clock.now == 105
        upstream.hook = None
        assert client.post("/add", json=addition()).status_code == 200
    assert len(upstream.writes) == 7 and len(upstream.memories) == 1
    assert all(call == upstream.writes[0] for call in upstream.writes)


@pytest.mark.parametrize(
    "status,error",
    [
        (500, "unrelated internal failure"),
        (500, '1064 (42000): SQL parser error: syntax error near "mem_memories"'),
        (500, '1064 (42000): SQL parser error: table "unrelated_table" does not exist'),
        (500, "1146 (42S02): no such table synthetic_db.mem_memories_suffix.not_a_table"),
        (401, PARSER_MISSING),
        (404, PARSER_MISSING),
        (422, PARSER_MISSING),
        (429, PARSER_MISSING),
        (503, PARSER_MISSING),
    ],
)
def test_unrelated_errors_are_not_retried(settings, upstream, clock, status, error):
    upstream.hook = lambda *_: httpx.Response(status, text=error)
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code != 200
    assert len(upstream.writes) == 1 and not clock.sleeps


@pytest.mark.parametrize(
    "path,body", [("/v1/memories", {}), (IDEMPOTENT_PATH, {"content": "synthetic"})]
)
def test_non_atomic_or_unkeyed_write_is_never_retried(settings, clock, path, body):
    calls = []

    def fail(request):
        calls.append(request)
        return httpx.Response(500, text=PARSER_MISSING)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(AdapterError) as caught:
            Memoria(settings, client).request("POST", path, "a" * 64, body)
    assert caught.value.write_not_sent is False
    assert len(calls) == 1 and not clock.sleeps


@pytest.mark.parametrize("limited_by", ["request", "visibility"])
def test_retry_obeys_existing_deadlines_and_releases_scheduler_slot(
    settings, clock, limited_by
):
    calls = []

    def fail(request):
        calls.append(request)
        return httpx.Response(500, text=EXECUTOR_MISSING)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        boundary = Memoria(replace(settings, backend_max_concurrency=1), client)
        sleep = clock.sleep

        def check_sleep(seconds):
            assert boundary.scheduler.active == 0
            sleep(seconds)

        clock.sleep = check_sleep
        with request_budget(0.25 if limited_by == "request" else 10):
            with pytest.raises(AdapterError) as caught:
                boundary.request(
                    "GET", "/v1/memories/synthetic", "a" * 64,
                    deadline=clock.now + 0.25 if limited_by == "visibility" else None,
                )
    assert caught.value.status == (504 if limited_by == "request" else 503)
    assert len(calls) == 1 and clock.sleeps == [0.25]
    assert boundary.scheduler.active == 0


def test_all_chunks_and_verification_share_one_request_window(settings, upstream, clock):
    write_calls = []

    def lag(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH:
            write_calls.append(body)
            if len(write_calls) == 1:
                return httpx.Response(500, text=PARSER_MISSING)
        if request.method == "GET":
            return httpx.Response(500, text=EXECUTOR_MISSING)

    upstream.hook = lag
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 502
        assert clock.now == 105 and sum(clock.sleeps) == 5
        assert len(upstream.writes) == 2 and len(upstream.memories) == 1
        upstream.hook = None
        assert client.post("/add", json=addition()).status_code == 200
    assert len(upstream.writes) == 2


def test_multi_message_add_does_not_reset_retry_window_per_chunk(settings, upstream, clock):
    def lag(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH:
            if len(upstream.writes) == 1 or body["extra_metadata"]["message_index"] == 1:
                return httpx.Response(500, text=PARSER_MISSING)

    upstream.hook = lag
    payload = addition(messages=[{"role": "user", "content": f"fact {i}"} for i in range(2)])
    with running(settings, upstream) as client:
        assert client.post("/add", json=payload).status_code == 502
    assert clock.now == 105 and sum(clock.sleeps) == 5
    assert len(upstream.memories) == 1


@pytest.mark.parametrize("seconds", [0, 0.2, 2, 5])
def test_configured_window_bounds_entire_recovery(settings, clock, seconds):
    calls = []

    def lag(request):
        calls.append(request)
        return httpx.Response(500, text=PARSER_MISSING)

    with httpx.Client(transport=httpx.MockTransport(lag)) as client:
        boundary = Memoria(replace(settings, catalog_retry_timeout_seconds=seconds), client)
        with pytest.raises(AdapterError):
            boundary.request("GET", "/v1/memories/synthetic", "a" * 64)
    assert clock.now == pytest.approx(100 + seconds)
    assert sum(clock.sleeps) == pytest.approx(seconds)
    if seconds == 0:
        assert len(calls) == 1 and not clock.sleeps
    for request in calls[1:]:
        assert max(request.extensions["timeout"].values()) <= seconds


def test_retry_http_time_consumes_window_and_late_success_is_not_accepted(settings, clock):
    calls = []

    def lag(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500, text=PARSER_MISSING)
        assert max(request.extensions["timeout"].values()) == 4.5
        clock.now += 4.6
        return httpx.Response(200, json={"synthetic": True})

    with httpx.Client(transport=httpx.MockTransport(lag)) as client:
        with pytest.raises(AdapterError, match="catalog retry window expired"):
            Memoria(settings, client).request("GET", "/v1/memories/synthetic", "a" * 64)
    assert len(calls) == 2 and clock.sleeps == [0.5]


def test_retry_http_failures_consume_the_same_window(settings, clock):
    calls = []

    def lag(request):
        calls.append(request)
        if len(calls) > 1:
            clock.now += min(1.0, max(request.extensions["timeout"].values()))
        return httpx.Response(500, text=PARSER_MISSING)

    with httpx.Client(transport=httpx.MockTransport(lag)) as client:
        with pytest.raises(AdapterError):
            Memoria(settings, client).request("GET", "/v1/memories/synthetic", "a" * 64)
    assert clock.now == 105
    assert sum(clock.sleeps) < 5


def test_retry_connect_failure_keeps_prior_write_outcome_ambiguous(settings, clock):
    calls = []

    def fail(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500, text=PARSER_MISSING)
        raise httpx.ConnectError("synthetic connection failure", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(AdapterError) as caught:
            Memoria(settings, client).store_idempotent(
                "a" * 64, {"content": "synthetic"}, "synthetic-key"
            )
    assert caught.value.status == 503 and caught.value.write_not_sent is False
    assert len(calls) == 2


def test_later_incoming_request_gets_a_fresh_window(settings, upstream, clock):
    def unavailable(request, body, owner):
        if request.url.path == IDEMPOTENT_PATH:
            return httpx.Response(500, text=PARSER_MISSING)

    upstream.hook = unavailable
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 502
        assert clock.now == 105
        failures = []

        def once(request, body, owner):
            if request.url.path == IDEMPOTENT_PATH:
                failures.append(True)
                if len(failures) == 1:
                    return httpx.Response(500, text=PARSER_MISSING)

        upstream.hook = once
        assert client.post("/add", json=addition()).status_code == 200
    assert clock.now == 105.5 and len(upstream.memories) == 1


@pytest.mark.parametrize("value", [-1, 121, float("nan"), float("inf")])
def test_invalid_catalog_retry_window_rejected(settings, value):
    with pytest.raises(ValueError, match="MEMORIA_CATALOG_RETRY_TIMEOUT_SECONDS"):
        replace(settings, catalog_retry_timeout_seconds=value)


def test_catalog_retry_window_from_environment(monkeypatch):
    from src.config import Settings

    monkeypatch.setenv("MEMORIA_URL", "http://memoria.test")
    monkeypatch.setenv("MEMORIA_MASTER_KEY", "synthetic")
    assert Settings.from_env().catalog_retry_timeout_seconds == 5
    monkeypatch.setenv("MEMORIA_CATALOG_RETRY_TIMEOUT_SECONDS", "2.5")
    assert Settings.from_env().catalog_retry_timeout_seconds == 2.5
