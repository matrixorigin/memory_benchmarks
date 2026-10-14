import json

import httpx
import pytest

from src.smoke import call_adapter


@pytest.fixture(autouse=True)
def deterministic_retry_jitter(monkeypatch):
    monkeypatch.setattr("src.smoke.random.uniform", lambda low, high: 0)


@pytest.mark.parametrize("path", ["/add", "/search"])
@pytest.mark.parametrize("failure", [408, 425, 429, 500, 502, 503, 504, "transport"])
def test_smoke_retries_transient_errors_with_identical_payload(monkeypatch, path, failure):
    bodies = []
    delays = []
    payload = {"request_id": "same-write", "user_id": "alice", "messages": ["same data"]}

    def backend(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            if failure == "transport":
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(failure, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("src.smoke.time.sleep", delays.append)
    with httpx.Client(transport=httpx.MockTransport(backend)) as client:
        assert call_adapter(client, "http://adapter.test", {}, path, payload) == {"ok": True}
    assert bodies == [payload, payload]
    assert delays == [2 if failure in {429, 503} else 0.25]


@pytest.mark.parametrize("status", [409, 524])
@pytest.mark.parametrize("path", ["/add", "/search"])
def test_smoke_add_only_retry_statuses(monkeypatch, path, status):
    calls = []
    monkeypatch.setattr("src.smoke.time.sleep", lambda _: None)

    def backend(request):
        calls.append(request)
        return httpx.Response(status if len(calls) == 1 else 200, json={})

    with httpx.Client(transport=httpx.MockTransport(backend)) as client:
        if path == "/add":
            call_adapter(client, "http://adapter.test", {}, path, {})
            assert len(calls) == 2
        else:
            with pytest.raises(httpx.HTTPStatusError):
                call_adapter(client, "http://adapter.test", {}, path, {})
            assert len(calls) == 1


@pytest.mark.parametrize("failure", [400, 401, 403, 404, 422, "bad_json"])
def test_smoke_permanent_errors_and_malformed_success_fail_immediately(monkeypatch, failure):
    calls = []
    delays = []
    monkeypatch.setattr("src.smoke.time.sleep", delays.append)

    def backend(request):
        calls.append(request)
        return httpx.Response(200 if failure == "bad_json" else failure, text="invalid-json")

    with httpx.Client(transport=httpx.MockTransport(backend)) as client:
        with pytest.raises(ValueError if failure == "bad_json" else httpx.HTTPStatusError):
            call_adapter(client, "http://adapter.test", {}, "/add", {})
    assert len(calls) == 1
    assert not delays


@pytest.mark.parametrize("failure", [503, "transport"])
def test_smoke_retry_budget_is_bounded(monkeypatch, failure):
    calls = []
    delays = []
    monkeypatch.setattr("src.smoke.time.sleep", delays.append)

    def backend(request):
        calls.append(request)
        if failure == "transport":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(failure)

    with httpx.Client(transport=httpx.MockTransport(backend)) as client:
        with pytest.raises(httpx.ReadTimeout if failure == "transport" else httpx.HTTPStatusError):
            call_adapter(client, "http://adapter.test", {}, "/add", {})
    assert len(calls) == 6
    assert delays == [0.25, 0.5, 1, 2, 4]


def test_503_retry_hint_is_honored_with_positive_jitter(monkeypatch):
    delays = []
    calls = []
    monkeypatch.setattr("src.smoke.random.uniform", lambda low, high: high)
    monkeypatch.setattr("src.smoke.time.sleep", delays.append)

    def backend(request):
        calls.append(request)
        return (
            httpx.Response(503, headers={"Retry-After": "4"})
            if len(calls) == 1
            else (httpx.Response(200, json={"ok": True}))
        )

    with httpx.Client(transport=httpx.MockTransport(backend)) as client:
        assert call_adapter(client, "http://adapter.test", {}, "/add", {}) == {"ok": True}
    assert delays == [5]


@pytest.mark.parametrize("hint", ["-1", "invalid", "1, 2", "９", "9" * 11])
def test_invalid_503_retry_hint_uses_exponential_backoff(monkeypatch, hint):
    calls = []
    delays = []
    monkeypatch.setattr("src.smoke.time.sleep", delays.append)

    def backend(request):
        calls.append(request)
        return (
            httpx.Response(503, headers={"Retry-After": hint.encode()})
            if len(calls) == 1
            else (httpx.Response(200, json={}))
        )

    with httpx.Client(transport=httpx.MockTransport(backend)) as client:
        call_adapter(client, "http://adapter.test", {}, "/add", {})
    assert delays == [0.25]
