import json
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest

from scripts import benchmark_ci as ci
from src import benchmark, evaluation


@pytest.mark.parametrize("failure", ["answer", "judge", "length", "429", "503", "transport"])
def test_failed_question_continues_and_resume_only_retries_pending_models(
    tmp_path,
    monkeypatch,
    fake_encoding,
    model_env,
    mock_http,
    failure,
):
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps(
            [
                {
                    "sample_id": "sample",
                    "conversation": {
                        "session_1_date_time": "date",
                        "session_1": [{"dia_id": "D1:1", "speaker": "A", "text": "fact"}],
                    },
                    "qa": [
                        {
                            "id": f"q{i}",
                            "question": f"question {i}",
                            "answer": "PRIVATE_GOLD",
                            "category": 1,
                            "evidence": ["D1:1"],
                        }
                        for i in (1, 2)
                    ],
                }
            ]
        )
    )
    calls = Counter()
    broken = True

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        host, path = request.url.host, request.url.path
        if host == "adapter.test":
            calls[path] += 1
            assert "PRIVATE_GOLD" not in str(body)
            if path == "/add":
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        **{key: body[key] for key in ("request_id", "user_id", "session_id")},
                    },
                )
            if path == "/search":
                return httpx.Response(
                    200, json={"data": [{"id": "m1", "content": "[user]\n[date] A: fact"}]}
                )
            return httpx.Response(200, json={"status": "ok"})
        text = body["messages"][-1]["content"]
        question = (
            json.loads(text)["question"]
            if host == "answer.test"
            else text.rsplit("Question: ", 1)[1].split("\nGold answer:", 1)[0]
        )
        calls[(host, question)] += 1
        if host == "answer.test":
            assert "PRIVATE_GOLD" not in str(body)
        bad_role = "answer.test" if failure == "answer" else "judge.test"
        failing = broken and question == "question 1" and host == bad_role
        if failing and failure in {"429", "503"}:
            return httpx.Response(int(failure), text="PRIVATE upstream secret")
        if failing and failure == "transport":
            raise httpx.ReadTimeout("PRIVATE upstream secret", request=request)
        content = "prediction" if host == "answer.test" else '{"label": "CORRECT"}'
        if failing and failure in {"answer", "judge"}:
            content = " " if failure == "answer" else "INVALID PRIVATE VERDICT"
        if failing and failure == "length":
            content = '{"label":'
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "length" if failing and failure == "length" else "stop",
                    }
                ]
            },
        )

    mock_http(handler)
    monkeypatch.setattr(evaluation.time, "sleep", lambda _: None)
    args = SimpleNamespace(
        dataset=source,
        kind="locomo",
        conversations=1,
        top_k=1,
        adapter_url="http://adapter.test",
        output=tmp_path / "run.json",
        resume=False,
        mode="answer-eval",
        private_dir=tmp_path / "private",
    )
    assert benchmark.run(args) == 1
    report = json.loads(args.output.read_text())
    assert report["status"] == "partial"
    assert report["questions"]["q2"]["evaluation_status"] == "completed"
    phase = "answer" if failure == "answer" else "judge"
    assert report["questions"]["q1"]["evaluation_status"] == f"{phase}_failed"
    for summary in (report["summary"], ci.metrics(report["questions"].values())):
        assert summary["evaluation_coverage"] == 0.5
        assert summary["scored_questions"] == 1
        assert summary["evaluation_pending_questions"] == 1
        assert summary[f"{phase}_failed_questions"] == 1
        assert summary["mean_local_judge_score"] == 1
    assert "PRIVATE" not in args.output.read_text()
    before = calls.copy()
    broken = False
    args.resume = True
    assert benchmark.run(args) == 0
    final = json.loads(args.output.read_text())
    assert final["status"] == "completed"
    assert final["summary"]["evaluation_coverage"] == 1
    assert final["summary"]["evaluation_pending_questions"] == 0
    assert "evaluation_error" not in final["questions"]["q1"]
    assert calls["/add"] == before["/add"] == 1
    assert calls["/search"] == before["/search"] == 2
    for host in ("answer.test", "judge.test"):
        assert calls[(host, "question 2")] == before[(host, "question 2")]
    assert calls[("answer.test", "question 1")] == before[("answer.test", "question 1")] + (
        1 if phase == "answer" else 0
    )
    assert calls[("judge.test", "question 1")] == before[("judge.test", "question 1")] + 1


def test_fatal_model_auth_stops_before_next_question(
    tmp_path,
    fake_encoding,
    model_env,
    mock_http,
):
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps(
            [
                {
                    "sample_id": "sample",
                    "conversation": {
                        "session_1_date_time": "date",
                        "session_1": [{"dia_id": "D1:1", "speaker": "A", "text": "fact"}],
                    },
                    "qa": [
                        {"id": f"q{i}", "question": f"q{i}", "answer": "gold", "category": 1}
                        for i in (1, 2)
                    ],
                }
            ]
        )
    )
    searches = []

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.host == "answer.test":
            return httpx.Response(401, text="PRIVATE credential detail")
        if request.url.path == "/add":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    **{key: body[key] for key in ("request_id", "user_id", "session_id")},
                },
            )
        if request.url.path == "/search":
            searches.append(body)
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200)

    mock_http(handler)
    args = SimpleNamespace(
        dataset=source,
        kind="locomo",
        conversations=1,
        top_k=1,
        adapter_url="http://adapter.test",
        output=tmp_path / "run.json",
        resume=False,
        mode="answer-eval",
        private_dir=tmp_path / "private",
    )
    assert benchmark.run(args) == 1
    report = json.loads(args.output.read_text())
    assert report["status"] == "failed"
    assert report["error"]["http_status"] == 401
    assert report["questions"]["q1"]["evaluation_error"]["fatal"]
    assert "q2" not in report["questions"]
    assert len(searches) == 1


def test_scoring_coverage_excludes_multimodal_and_missing_gold():
    records = [
        {"skipped": "multimodal"},
        {"evaluation": {"skipped": "missing_gold_answer"}},
        {"evaluation": {"local_judge_score": 0}},
        {"evaluation_pending": True, "evaluation_status": "answer_failed", "evaluation": {}},
    ]
    for summary in (
        benchmark.summarize({"completed_adds": [], "questions": dict(enumerate(records))}),
        ci.metrics(records),
    ):
        assert summary["evaluation_eligible_questions"] == 2
        assert summary["evaluation_coverage"] == 0.5
        assert summary["mean_local_judge_score"] == 0
