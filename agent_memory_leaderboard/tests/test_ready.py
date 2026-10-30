from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        api_key="client-secret",
        state_dir=tmp_path,
    )


def test_ready_healthy_is_unauthenticated_and_does_not_write(settings):
    calls = []

    def healthy(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url == "http://memoria.test/health"
        assert request.extensions["timeout"]["read"] == 3
        return httpx.Response(200, json={"status": "ok"})

    with httpx.Client(transport=httpx.MockTransport(healthy)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            response = client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert len(calls) == 1


@pytest.mark.parametrize("failure", [503, 302, "connection", "timeout"])
def test_ready_upstream_unavailable(settings, failure):
    def unavailable(request):
        if failure == "connection":
            raise httpx.ConnectError("upstream-secret", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("upstream-secret", request=request)
        return httpx.Response(failure, text="upstream-secret")

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            response = client.get("/ready")
            assert response.status_code == 503
            assert response.json() == {"status": "not_ready"}
            assert "upstream-secret" not in response.text
            assert client.get("/health").status_code == 200


def test_ready_journal_unavailable_does_not_call_upstream(settings):
    def unexpected(request):
        raise AssertionError("Upstream must not be called when journal is unavailable")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            journal = settings.state_dir / "requests.sqlite3"
            backup = settings.state_dir / "saved.sqlite3"
            journal.rename(backup)
            journal.mkdir()  # sqlite3 cannot open a directory as its database.
            try:
                response = client.get("/ready")
                assert response.status_code == 503
                assert response.json() == {"status": "not_ready"}
                assert client.get("/health").status_code == 200
            finally:
                journal.rmdir()
                backup.rename(journal)


def test_probes_work_while_business_threads_are_saturated(settings):
    entered = 0
    lock = Lock()
    saturated = Event()
    release = Event()

    def upstream(request):
        nonlocal entered
        if request.url.path == "/v1/memories/search":
            with lock:
                entered += 1
                if entered == 40:
                    saturated.set()
            assert release.wait(30), "Search barrier was not released"
            return httpx.Response(200, json=[])
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            with ThreadPoolExecutor(max_workers=42) as pool:
                searches = [
                    pool.submit(
                        client.post,
                        "/search",
                        headers={"Authorization": "Bearer client-secret"},
                        json={"user_id": f"user-{i}", "query": "test", "top_k": 1},
                    )
                    for i in range(40)
                ]
                try:
                    assert saturated.wait(10), "Business thread pool was not saturated"
                    health = pool.submit(client.get, "/health")
                    ready = pool.submit(client.get, "/ready")
                    assert health.result(timeout=2).status_code == 200
                    assert ready.result(timeout=2).status_code == 200
                    assert not any(search.done() for search in searches)
                finally:
                    release.set()
                assert all(search.result(timeout=5).status_code == 200 for search in searches)


def test_readiness_has_no_waiting_queue_and_does_not_block_liveness(settings):
    entered = Event()
    release = Event()
    calls = []

    def slow_health(request):
        calls.append(request)
        entered.set()
        assert release.wait(30), "Readiness barrier was not released"
        return httpx.Response(200, json={"status": "ok"})

    with httpx.Client(transport=httpx.MockTransport(slow_health)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            with ThreadPoolExecutor(max_workers=3) as pool:
                first = pool.submit(client.get, "/ready")
                try:
                    assert entered.wait(5)
                    second = pool.submit(client.get, "/ready")
                    health = pool.submit(client.get, "/health")
                    assert second.result(timeout=2).status_code == 503
                    assert health.result(timeout=2).status_code == 200
                    assert len(calls) == 1
                finally:
                    release.set()
                assert first.result(timeout=5).status_code == 200
            # The probe slot is returned after completion.
            assert client.get("/ready").status_code == 200


def test_readiness_does_not_queue_behind_an_active_journal_transaction(settings):
    from src.journal import Journal

    # The probe uses its own read path, independent of the process-local mutex
    # and the cross-process gate held by a different Journal instance.
    journal = Journal(settings.state_dir)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as http:
        with TestClient(create_app(settings, client=http)) as client:
            with ThreadPoolExecutor() as pool:
                with journal.connect("record") as db:
                    db.execute(
                        "INSERT INTO requests(key, fingerprint, complete) VALUES (?, ?, 0)",
                        ("uncommitted", "fingerprint"),
                    )
                    probe = pool.submit(client.get, "/ready")
                    assert probe.result(timeout=1).status_code == 200


def test_readiness_busy_read_is_bounded_and_not_reported_healthy(settings):
    import sqlite3
    import time

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as http:
        with TestClient(create_app(settings, client=http)) as client:
            with sqlite3.connect(settings.state_dir / "requests.sqlite3") as blocker:
                blocker.execute("BEGIN EXCLUSIVE")
                started = time.monotonic()
                assert client.get("/ready").status_code == 503
                assert time.monotonic() - started < 1
            assert client.get("/ready").status_code == 200


def test_readiness_does_not_recreate_missing_journal(settings):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as http:
        with TestClient(create_app(settings, client=http)) as client:
            path = settings.state_dir / "requests.sqlite3"
            backup = path.with_suffix(".backup")
            path.rename(backup)
            try:
                assert client.get("/ready").status_code == 503
                assert not path.exists()
            finally:
                backup.rename(path)
