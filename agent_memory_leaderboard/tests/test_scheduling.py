import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_endpoints import FakeMemoria

from src.app import create_app
from src.config import Settings
from src.errors import AdapterError
from src.scheduling import BackendScheduler


def test_sixteen_adds_queue_native_calls_and_probes_stay_available(tmp_path):
    fake = FakeMemoria()
    active = 0
    peak = 0
    starts = []
    first_call = threading.Event()
    release = threading.Event()
    guard = threading.Lock()

    def transport(request):
        nonlocal active, peak
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        with guard:
            active += 1
            peak = max(peak, active)
            starts.append(time.monotonic())
        first_call.set()
        assert release.wait(5)
        try:
            time.sleep(0.003)
            return fake(request)
        finally:
            with guard:
                active -= 1

    settings = Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        state_dir=tmp_path,
        backend_max_concurrency=1,
        backend_requests_per_second=200,
        backend_queue_timeout_seconds=5,
    )
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            with ThreadPoolExecutor(max_workers=16) as pool:
                futures = [
                    pool.submit(
                        client.post,
                        "/add",
                        json={
                            "request_id": f"request-{i}",
                            "user_id": f"user-{i}",
                            "session_id": "session",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": f"Synthetic project {i} uses Rust.",
                                }
                            ],
                        },
                    )
                    for i in range(16)
                ]
                try:
                    assert first_call.wait(5)
                    assert client.get("/health").status_code == 200
                    assert client.get("/ready").status_code == 200
                finally:
                    release.set()
                for future in futures:
                    response = future.result(timeout=10)
                    assert response.status_code == 200, response.text
                    assert response.json()["success"] is True
            assert peak == 1
            assert len(fake.writes) == 16
            assert len(starts) == 48  # store, point read and search visibility per Add
            assert all(b - a >= 0.004 for a, b in zip(starts, starts[1:]))
            for i in range(16):
                response = client.post(
                    "/search",
                    json={
                        "user_id": f"user-{i}",
                        "query": "Which language?",
                        "top_k": 100,
                    },
                )
                assert response.status_code == 200
                assert [m["content"] for m in response.json()["data"]] == [
                    f"[user]\nSynthetic project {i} uses Rust."
                ]


def test_timeout_never_enters_backend_and_frees_queue():
    scheduler = BackendScheduler(1, 0, 0.03)
    with scheduler.slot():
        with ThreadPoolExecutor(max_workers=1) as pool:

            def waiting_call():
                with scheduler.slot():
                    pytest.fail("Timed-out caller must not reach the backend")

            with pytest.raises(AdapterError) as error:
                pool.submit(waiting_call).result(timeout=2)
            assert error.value.write_not_sent
            assert error.value.status == 503
            assert not scheduler.waiters
    with scheduler.slot():
        pass
    assert scheduler.active == 0


def test_backend_exception_releases_capacity():
    scheduler = BackendScheduler(1, 0, 1)
    with pytest.raises(RuntimeError), scheduler.slot():
        raise RuntimeError("backend failed")
    with scheduler.slot():
        pass


@pytest.mark.parametrize(
    "change",
    [
        {"backend_max_concurrency": -1},
        {"backend_requests_per_second": -1},
        {"backend_requests_per_second": float("inf")},
        {"backend_queue_timeout_seconds": 0},
        {"backend_queue_timeout_seconds": float("nan")},
    ],
)
def test_invalid_budget_rejected(change):
    with pytest.raises(ValueError):
        replace(Settings(memoria_url="http://memoria.test", memoria_master_key="test"), **change)
