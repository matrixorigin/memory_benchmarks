import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src import probes


def args(tmp_path, **overrides):
    return SimpleNamespace(
        **{
            "output": tmp_path / "probe.json",
            "mode": "capacity",
            "execute": False,
            "adapter_url": "http://adapter.test",
            "reference_time": "2026-01-01T00:00:00Z",
            "concurrency": 1,
            "qps": 20,
            "requests": 2,
            "messages_per_add": 1,
            "message_bytes": 32,
            "explain": False,
            "route": "add",
            "duration": 600,
            "timeout": 1800,
            **overrides,
        }
    )


@pytest.mark.parametrize("mode", ["capacity", "source-time"])
def test_plan_default_sends_no_traffic(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(probes.httpx, "Client", lambda **kw: pytest.fail("unexpected traffic"))
    options = args(tmp_path, mode=mode)
    assert probes.run(options) == 0
    assert json.loads(options.output.read_text())["status"] == "plan-only"
    with pytest.raises(ValueError, match="fresh output"):
        probes.run(options)


@pytest.mark.parametrize(
    "option,value",
    [
        ("--concurrency", "9"),
        ("--requests", "1001"),
        ("--qps", "21"),
        ("--qps", "nan"),
        ("--message-bytes", "32769"),
        ("--messages-per-add", "101"),
        ("--duration", "3601"),
        ("--timeout", "1801"),
    ],
)
def test_cli_rejects_workload_outside_bounds(tmp_path, monkeypatch, option, value):
    monkeypatch.setattr(
        sys, "argv", ["probe", "capacity", "--output", str(tmp_path / "p.json"), option, value]
    )
    with pytest.raises(SystemExit) as error:
        probes.main()
    assert error.value.code == 2


def test_time_boundaries_and_explain_allowlist():
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    cases = probes.time_cases(anchor)
    assert cases["old"] < cases["after_cutoff"] < cases["before_cutoff"] < cases["recent"]
    assert cases["recent"] == int(anchor.timestamp() * 1000)
    assert probes.safe_explain(
        {
            "path": "vector",
            "result_count": 3,
            "total_ms": 1.5,
            "vector_hit": True,
            "embedding_ms": float("inf"),
            "content": "PRIVATE",
            "secret": "PRIVATE",
        }
    ) == {
        "path": "vector",
        "result_count": 3,
        "total_ms": 1.5,
        "vector_hit": True,
    }


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 1.0

        def advance(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(probes.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(probes.time, "sleep", clock.advance)
    return clock


def fake_call(calls, clock, attempts=(0.01,), fail=False):
    def call(client, url, headers, path, body, observe):
        calls.append((path, body))
        for index, seconds in enumerate(attempts):
            clock.advance(seconds)
            observe(
                {
                    "route": path,
                    "status": 200 if index == len(attempts) - 1 else 503,
                    "reason": "none",
                    "will_retry": index < len(attempts) - 1,
                    "elapsed_seconds": seconds,
                }
            )
        if fail:
            raise ValueError("PRIVATE secret exception")
        if path == "/add":
            return {
                "success": True,
                **{key: body[key] for key in ("request_id", "user_id", "session_id")},
            }
        return {"data": []}

    return call


def test_source_time_execution_isolates_users_and_sends_actual_timestamps(
    tmp_path,
    monkeypatch,
    model_env,
    clock,
):
    calls = []
    monkeypatch.setattr(probes, "call_adapter", fake_call(calls, clock))
    options = args(tmp_path, mode="source-time", execute=True)
    assert probes.run(options) == 0
    report = json.loads(options.output.read_text())
    adds = [body for path, body in calls if path == "/add"]
    assert len(adds) == 4
    assert len({body["user_id"] for body in adds}) == 4
    assert sum(path == "/search" for path, _ in calls) == 12
    assert {m["timestamp"] for body in adds for m in body["messages"]} == set(
        report["source_timestamps_ms"].values()
    )
    assert report["privacy"]["delete_by"]


@pytest.mark.parametrize("attempts,verified", [((301,), True), ((160, 160), False)])
def test_long_request_requires_single_successful_attempt_over_five_minutes(
    tmp_path,
    monkeypatch,
    model_env,
    clock,
    attempts,
    verified,
):
    calls = []
    monkeypatch.setattr(probes, "call_adapter", fake_call(calls, clock, attempts))
    options = args(tmp_path, execute=True, requests=1)
    assert probes.run(options) == 0
    assert json.loads(options.output.read_text())["long_request_verified"] is verified


def test_capacity_deadline_skips_queued_work_and_keeps_unique_add_ids(
    tmp_path,
    monkeypatch,
    model_env,
    clock,
):
    calls = []
    monkeypatch.setattr(probes, "call_adapter", fake_call(calls, clock, (1.1,)))
    options = args(tmp_path, execute=True, requests=3, duration=1)
    assert probes.run(options) == 1
    report = json.loads(options.output.read_text())
    assert report["status"] == "partial"
    assert report["skipped_requests"] == 2
    assert len(calls) == 1
    assert calls[0][1]["request_id"].endswith(":add:0")


def test_capacity_seed_failure_stops_search_and_redacts_errors(
    tmp_path,
    monkeypatch,
    model_env,
    clock,
):
    calls = []
    monkeypatch.setattr(probes, "call_adapter", fake_call(calls, clock, fail=True))
    options = args(tmp_path, execute=True, route="search")
    assert probes.run(options) == 1
    report = json.loads(options.output.read_text())
    assert report["status"] == "failed"
    assert report["failed_requests"] == 1
    assert [path for path, _ in calls] == ["/add"]
    assert "PRIVATE" not in options.output.read_text()


def test_capacity_does_not_sleep_beyond_scheduling_deadline(
    tmp_path,
    monkeypatch,
    model_env,
    clock,
):
    calls = []
    monkeypatch.setattr(probes, "call_adapter", fake_call(calls, clock))
    options = args(tmp_path, execute=True, requests=3, qps=0.1, duration=1)
    assert probes.run(options) == 1
    report = json.loads(options.output.read_text())
    assert report["skipped_requests"] == 2
    assert clock.now < 2


def test_capacity_adds_use_unique_request_ids(tmp_path, monkeypatch, model_env, clock):
    calls = []
    monkeypatch.setattr(probes, "call_adapter", fake_call(calls, clock))
    assert probes.run(args(tmp_path, execute=True, requests=3)) == 0
    ids = [body["request_id"] for _, body in calls]
    assert len(set(ids)) == 3
    assert all(identity.endswith(f":add:{index}") for index, identity in enumerate(ids))
