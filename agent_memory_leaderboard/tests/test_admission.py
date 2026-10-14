import sqlite3
import threading
import time

import anyio
import httpx
from test_endpoints import FakeMemoria

from src.app import create_app
from src.config import Settings
from src.scheduling import RequestAdmission


def test_over_40_requests_expire_before_threads_or_journal_even_with_shared_pool_saturated(
    tmp_path,
):
    async def scenario():
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = 40
        shared_saturated = threading.Event()
        release_shared = threading.Event()
        native_entered = threading.Event()
        release_native = threading.Event()
        guard = threading.Lock()
        shared_count = 0
        native_count = 0
        fake = FakeMemoria()
        responses = []

        def occupy_shared_thread():
            nonlocal shared_count
            with guard:
                shared_count += 1
                if shared_count == 40:
                    shared_saturated.set()
            assert release_shared.wait(5)

        async def occupy_shared():
            await anyio.to_thread.run_sync(occupy_shared_thread)

        def backend(request):
            nonlocal native_count
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            native_count += 1
            if native_count == 1:
                native_entered.set()
                assert release_native.wait(5)
            return fake(request)

        settings = Settings(
            memoria_url="http://memoria.test",
            memoria_master_key="upstream-secret",
            api_key="client-secret",
            state_dir=tmp_path,
            max_active_requests=1,
            backend_max_concurrency=1,
            backend_queue_timeout_seconds=0.05,
        )
        headers = {"Authorization": "Bearer client-secret"}
        with httpx.Client(transport=httpx.MockTransport(backend)) as native:
            app = create_app(settings, client=native)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://adapter.test",
            ) as http:

                async def search_first():
                    response = await http.post(
                        "/search",
                        headers=headers,
                        json={
                            "user_id": "first",
                            "query": "synthetic",
                            "top_k": 1,
                        },
                    )
                    assert response.status_code == 200

                async def queued(index):
                    if index % 2:
                        path = "/add"
                        body = {
                            "request_id": f"request-{index}",
                            "user_id": f"user-{index}",
                            "session_id": "session",
                            "messages": [
                                {"role": "user", "content": "Synthetic project uses Rust."}
                            ],
                        }
                    else:
                        path = "/search"
                        body = {"user_id": f"user-{index}", "query": "Rust", "top_k": 1}
                    start = time.monotonic()
                    response = await http.post(path, headers=headers, json=body)
                    responses.append((response, time.monotonic() - start))

                async with anyio.create_task_group() as active:
                    try:
                        for _ in range(40):
                            active.start_soon(occupy_shared)
                        with anyio.fail_after(3):
                            while not shared_saturated.is_set():
                                await anyio.sleep(0.001)
                        active.start_soon(search_first)
                        with anyio.fail_after(3):
                            while not native_entered.is_set():
                                await anyio.sleep(0.001)
                        with anyio.fail_after(2):
                            async with anyio.create_task_group() as waiting:
                                for index in range(64):
                                    waiting.start_soon(queued, index)
                        assert len(responses) == 64
                        assert all(response.status_code == 503 for response, _ in responses)
                        assert all(0.04 <= elapsed < 1 for _, elapsed in responses)
                        assert all(
                            2 <= int(response.headers["Retry-After"]) <= 60
                            for response, _ in responses
                        )
                        assert native_count == 1  # expired requests never entered the backend
                        assert (await http.get("/ready")).status_code == 200
                        assert (await http.get("/health")).status_code == 200
                        assert (
                            await http.post(
                                "/search",
                                json={
                                    "user_id": "bad-key",
                                    "query": "Rust",
                                    "top_k": 1,
                                },
                            )
                        ).status_code == 401
                        with sqlite3.connect(tmp_path / "requests.sqlite3") as journal:
                            assert (
                                journal.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
                            )
                    finally:
                        release_native.set()
                        release_shared.set()
                # Queue expiry does not poison admission or write-recovery state.
                response = await http.post(
                    "/add",
                    headers=headers,
                    json={
                        "request_id": "request-1",
                        "user_id": "user-1",
                        "session_id": "session",
                        "messages": [{"role": "user", "content": "Synthetic project uses Rust."}],
                    },
                )
                assert response.status_code == 200
                assert len(fake.writes) == 1

    anyio.run(scenario)


def test_cancelled_admission_waiter_is_removed_and_capacity_recovers():
    async def scenario():
        admission = RequestAdmission(1, 1)
        async with admission.slot():
            with anyio.move_on_after(0.01) as scope:
                async with admission.slot():
                    raise AssertionError("Cancelled waiter entered")
            assert scope.cancel_called
            assert admission.permits.statistics().tasks_waiting == 0
        async with admission.slot():
            pass
        assert admission.permits.value == 1

    anyio.run(scenario)


def test_cancelling_inflight_add_keeps_admission_until_write_is_journaled(tmp_path):
    async def scenario():
        entered = threading.Event()
        release = threading.Event()
        fake = FakeMemoria()
        scopes = []

        def backend(request):
            if request.url.path == "/v1/memories":
                entered.set()
                assert release.wait(5)
            return fake(request)

        settings = Settings(
            memoria_url="http://memoria.test",
            memoria_master_key="upstream-secret",
            state_dir=tmp_path,
            max_active_requests=1,
            backend_max_concurrency=1,
            backend_queue_timeout_seconds=0.03,
        )
        payload = {
            "request_id": "original",
            "user_id": "user",
            "session_id": "session",
            "messages": [{"role": "user", "content": "Synthetic project uses Rust."}],
        }
        with httpx.Client(transport=httpx.MockTransport(backend)) as native:
            app = create_app(settings, client=native)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://adapter.test",
            ) as http:

                async def original():
                    with anyio.CancelScope() as scope:
                        scopes.append(scope)
                        await http.post("/add", json=payload)

                async with anyio.create_task_group() as group:
                    group.start_soon(original)
                    try:
                        with anyio.fail_after(3):
                            while not entered.is_set():
                                await anyio.sleep(0.001)
                        scopes[0].cancel()
                        response = await http.post(
                            "/search",
                            json={
                                "user_id": "other",
                                "query": "Rust",
                                "top_k": 1,
                            },
                        )
                        assert response.status_code == 503
                        assert len(fake.writes) == 0
                    finally:
                        release.set()
                with sqlite3.connect(tmp_path / "requests.sqlite3") as journal:
                    assert journal.execute("SELECT complete FROM requests").fetchone()[0] == 1
                assert len(fake.writes) == 1
                assert (await http.post("/add", json=payload)).status_code == 200
                assert len(fake.writes) == 1

    anyio.run(scenario)
