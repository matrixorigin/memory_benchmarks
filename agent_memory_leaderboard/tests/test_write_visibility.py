import copy
import time
from contextlib import nullcontext
from dataclasses import replace

import httpx
import pytest

from src import memoria as memoria_module
from src.config import Settings
from src.errors import AdapterError
from src.journal import IDEMPOTENT_PROTOCOL, LEGACY_PROTOCOL, TOKEN_PROTOCOL, Journal
from src.memoria import Memoria
from src.models import AddRequest
from src.scheduling import BackendScheduler
from src.service import MemoryAdapter, digest
from tests.test_endpoints import addition, running
from tests.test_endpoints import settings as settings
from tests.test_endpoints import upstream as upstream


def test_temporarily_empty_read_completes_same_add_without_reposting(settings, upstream):
    reads = []

    def lag(request, body, owner):
        if request.method == "GET":
            reads.append(True)
            if len(reads) == 1:
                return httpx.Response(200, content="null")

    upstream.hook = lag
    with running(settings, upstream) as client:
        assert client.post("/add", json=addition()).status_code == 200
    assert len(reads) == 2
    assert len(upstream.writes) == len(upstream.memories) == 1


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(memoria_module, "time", clock)
    return clock


@pytest.fixture
def boundary(settings, upstream):
    settings = replace(settings, visibility_timeout_seconds=0.4)
    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        yield Memoria(settings, client)


def stored(boundary):
    owner = "a" * 64
    marker = "synthetic-marker"
    body = {"content": "Synthetic visibility fact.", "extra_metadata": {"aml_write": marker}}
    memory = boundary.store_idempotent(owner, body, marker)
    return owner, marker, body, memory


def test_delayed_search_reuses_point_read_and_reduces_http_timeouts(boundary, upstream, clock):
    owner, marker, _, memory = stored(boundary)
    probes = []

    def lag(request, body, owner):
        probes.append((request.method, dict(request.extensions["timeout"])))
        if request.url.path == "/v1/memories/search" and len(probes) < 4:
            return httpx.Response(200, json=[])

    upstream.hook = lag
    assert boundary.wait_for_visibility(owner, memory.memory_id, marker) == memory
    assert [m for m, _ in probes] == ["GET", "POST", "POST", "POST"]
    budgets = [max(t.values()) for _, t in probes]
    assert all(0 < b <= 0.4 + 1e-9 for b in budgets)
    assert budgets[-1] < budgets[0]
    assert len(upstream.writes) == 1


@pytest.mark.parametrize("protocol", [LEGACY_PROTOCOL, IDEMPOTENT_PROTOCOL])
def test_known_id_inflight_recovery_waits_without_reposting(
    boundary, upstream, clock, tmp_path, protocol
):
    owner, marker, body, memory = stored(boundary)
    journal = Journal(tmp_path)
    journal.record("synthetic", 0, "inflight", memory.memory_id, protocol=protocol)
    reads = []

    def lag(request, body, owner):
        if request.method == "GET":
            reads.append(True)
            if len(reads) == 1:
                return httpx.Response(200, content="null")

    upstream.hook = lag
    MemoryAdapter(boundary.settings, boundary, journal).write(
        owner, "synthetic", 0, body, journal.state("synthetic", 0)
    )
    assert len(reads) == 2 and len(upstream.writes) == 1
    assert journal.state("synthetic", 0) == ("stored", memory.memory_id, protocol)


@pytest.mark.parametrize("hidden_stage", ["read", "search"])
def test_visibility_expiry_preserves_inflight_and_same_id_retry_recovers(
    boundary, upstream, clock, tmp_path, hidden_stage
):
    journal = Journal(tmp_path)
    adapter = MemoryAdapter(boundary.settings, boundary, journal)

    def hidden(request, body, owner):
        if request.method == "GET" and hidden_stage == "read":
            return httpx.Response(200, content="null")
        if request.url.path == "/v1/memories/search" and hidden_stage == "search":
            return httpx.Response(200, json=[])

    upstream.hook = hidden
    request = addition()
    model = AddRequest(**request)
    with pytest.raises(AdapterError) as caught:
        adapter.add(model)
    assert caught.value.status == 503 and "visibility timed out" in caught.value.reason
    assert clock.now == pytest.approx(100.4)
    key = digest(
        [boundary.settings.memoria_url.rstrip("/"), adapter.owner(model.user_id), model.request_id]
    )
    state, memory_id, protocol = journal.state(key, 0)
    assert state == "inflight" and memory_id and protocol == TOKEN_PROTOCOL
    assert journal.begin(key, digest(model.model_dump())) is False
    assert len(upstream.writes) == 1
    upstream.hook = None
    adapter.add(model)
    assert journal.begin(key, digest(model.model_dump())) is True
    assert len(upstream.writes) == len(upstream.memories) == 1


def test_read_and_search_share_one_budget_and_late_success_is_rejected(boundary, upstream, clock):
    owner, marker, _, memory = stored(boundary)
    probes = []

    def slow(request, body, owner):
        probes.append(request.method)
        if request.method == "GET":
            clock.now += 0.3
        else:
            assert max(request.extensions["timeout"].values()) == pytest.approx(0.1)
            clock.now += 0.2

    upstream.hook = slow
    with pytest.raises(AdapterError, match="visibility timed out"):
        boundary.wait_for_visibility(owner, memory.memory_id, marker)
    assert probes == ["GET", "POST"] and not clock.sleeps
    assert len(upstream.writes) == 1


@pytest.mark.parametrize(
    "kind",
    [
        "owner",
        "id",
        "marker",
        "inactive",
        "json",
        "http401",
        "http503",
        "search_json",
        "search_marker",
    ],
)
def test_permanent_or_malformed_results_are_not_retried(boundary, upstream, clock, kind):
    owner, marker, _, memory = stored(boundary)
    probes = []

    def invalid(request, body, current_owner):
        if kind.startswith("search_") and request.method == "GET":
            return None
        probes.append(request.method)
        if kind.startswith("http"):
            return httpx.Response(int(kind[4:]))
        if kind == "json":
            return httpx.Response(200, content="not-json")
        if kind == "search_json":
            return httpx.Response(200, json={"items": []})
        value = copy.deepcopy(memory.model_dump())
        if kind == "owner":
            value["user_id"] = "wrong-owner"
        elif kind == "id":
            value["memory_id"] = "wrong-id"
        elif kind in {"marker", "search_marker"}:
            value["extra_metadata"]["aml_write"] = "wrong-marker"
        elif kind == "inactive":
            value["is_active"] = False
        return httpx.Response(200, json=[value] if kind.startswith("search_") else value)

    upstream.hook = invalid
    with pytest.raises(AdapterError):
        boundary.wait_for_visibility(owner, memory.memory_id, marker)
    assert len(probes) == 1 and not clock.sleeps
    assert len(upstream.writes) == 1


@pytest.mark.parametrize("value", [0, -1, 121, float("nan"), float("inf")])
def test_invalid_visibility_budget_rejected(value):
    with pytest.raises(ValueError, match="MEMORIA_VISIBILITY_TIMEOUT_SECONDS"):
        Settings(
            memoria_url="http://memoria.test",
            memoria_master_key="synthetic",
            visibility_timeout_seconds=value,
        )


def test_visibility_budget_from_environment(monkeypatch):
    monkeypatch.setenv("MEMORIA_URL", "http://memoria.test")
    monkeypatch.setenv("MEMORIA_MASTER_KEY", "synthetic")
    monkeypatch.setenv("MEMORIA_VISIBILITY_TIMEOUT_SECONDS", "2.5")
    assert Settings.from_env().visibility_timeout_seconds == 2.5


@pytest.mark.parametrize("limited_by", ["capacity", "rate"])
def test_visibility_deadline_also_limits_scheduler_wait(boundary, upstream, limited_by):
    owner, marker, _, memory = stored(boundary)
    boundary.settings = replace(boundary.settings, visibility_timeout_seconds=0.03)
    scheduler = BackendScheduler(
        1 if limited_by == "capacity" else 0, 10 if limited_by == "rate" else 0, 10
    )
    boundary.scheduler = scheduler
    guard = scheduler.slot() if limited_by == "capacity" else nullcontext()
    if limited_by == "rate":
        scheduler.next_start = time.monotonic() + 1
    calls_before = len(upstream.calls)
    with guard:
        started = time.monotonic()
        with pytest.raises(AdapterError) as caught:
            boundary.wait_for_visibility(owner, memory.memory_id, marker)
        assert caught.value.status == 503
        assert time.monotonic() - started < 0.5
        assert not scheduler.waiters
    assert scheduler.active == 0
    assert len(upstream.calls) == calls_before


@pytest.mark.parametrize(
    "kind", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectTimeout, httpx.PoolTimeout]
)
@pytest.mark.parametrize("stage", ["read", "search"])
def test_visibility_limited_http_timeout_has_retryable_visibility_contract(
    boundary, upstream, clock, kind, stage
):
    owner, marker, _, memory = stored(boundary)

    def timeout(request, body, owner):
        if stage == "search" and request.method == "GET":
            return None
        raise kind("synthetic timeout", request=request)

    upstream.hook = timeout
    with pytest.raises(AdapterError) as caught:
        boundary.wait_for_visibility(owner, memory.memory_id, marker)
    assert caught.value.status == 503
    assert "visibility timed out" in caught.value.reason
    assert caught.value.retry_after == "1"
    assert not clock.sleeps and len(upstream.writes) == 1


@pytest.mark.parametrize("seconds,hint", [(0.4, "1"), (10, "10"), (60, "60"), (120, "60")])
def test_visibility_retry_hint_scales_and_is_bounded(boundary, upstream, clock, seconds, hint):
    owner, marker, _, memory = stored(boundary)
    boundary.settings = replace(boundary.settings, visibility_timeout_seconds=seconds)

    def expired(request, body, owner):
        clock.now += seconds
        return httpx.Response(200, content="null")

    upstream.hook = expired
    with pytest.raises(AdapterError) as caught:
        boundary.wait_for_visibility(owner, memory.memory_id, marker)
    assert caught.value.status == 503 and caught.value.retry_after == hint
    assert not clock.sleeps


@pytest.mark.parametrize("kind,status", [(httpx.ReadTimeout, 504), (httpx.ConnectTimeout, 503)])
def test_shorter_normal_http_timeout_keeps_original_mapping(
    boundary, upstream, clock, kind, status
):
    owner, marker, _, memory = stored(boundary)
    boundary.settings = replace(boundary.settings, timeout_seconds=0.01)

    def timeout(request, body, owner):
        raise kind("synthetic timeout", request=request)

    upstream.hook = timeout
    with pytest.raises(AdapterError) as caught:
        boundary.wait_for_visibility(owner, memory.memory_id, marker)
    assert caught.value.status == status and caught.value.retry_after is None
    assert "visibility timed out" not in caught.value.reason
    assert not clock.sleeps
