import json
from collections import Counter
from types import SimpleNamespace

import httpx

from src import benchmark, evaluation
from src.embedding_tokens import DEFAULT_TOKEN_BUDGET, token_policy


def test_longmemeval_resume_reuses_add_search_and_answer(
    tmp_path,
    monkeypatch,
    model_env,
    fake_encoding,
    mock_http,
):
    monkeypatch.setenv("BENCHMARK_INPUT_BUDGET", "128000")
    monkeypatch.setattr(evaluation.time, "sleep", lambda _: None)
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            [
                {
                    "question_id": "synthetic-question",
                    "question_type": "single-session-user",
                    "question": "Box color?",
                    "question_date": "2025/02/01 (Sat) 00:00",
                    "answer": "PRIVATE_GOLD",
                    "answer_session_ids": ["s1"],
                    "haystack_session_ids": ["s1"],
                    "haystack_dates": ["2025/01/01 (Wed) 00:00"],
                    "haystack_sessions": [
                        [
                            {"role": "user", "content": "Blue box.", "has_answer": True},
                            {"role": "assistant", "content": "Noted."},
                        ]
                    ],
                }
            ]
        )
    )
    calls, stored = Counter(), []
    broken = True

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        host, route = request.url.host, request.url.path
        calls[(host, route)] += 1
        if host == "adapter.test":
            assert "PRIVATE_GOLD" not in str(body) and "has_answer" not in str(body)
            if route == "/diagnostics/render-policy":
                return httpx.Response(200, json={"policy": token_policy(DEFAULT_TOKEN_BUDGET)})
            if route == "/add":
                stored.extend(body["messages"])
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        **{k: body[k] for k in ("request_id", "user_id", "session_id")},
                    },
                )
            if route == "/search":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": str(i), "content": f"[{message['role']}]\n{message['content']}"}
                            for i, message in reversed(list(enumerate(stored)))
                        ]
                    },
                )
            return httpx.Response(200, json={"status": "ok"})
        if host == "answer.test":
            assert len(body["messages"]) == 1
            text = body["messages"][0]["content"]
            assert "PRIVATE_GOLD" not in text and "has_answer" not in text
            assert text.index("Blue box.") < text.index("Noted.")
            assert "Current Date: 2025/02/01 (Sat) 00:00" in text
            assert body["max_tokens"] == 800
            text = "Blue"
        else:
            if broken:
                return httpx.Response(503)
            text = "yes"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": text}, "finish_reason": "stop"},
                ]
            },
        )

    mock_http(handler)
    args = SimpleNamespace(
        dataset=path,
        kind="longmemeval",
        conversations=1,
        top_k=3,
        adapter_url="http://adapter.test",
        output=tmp_path / "run.json",
        resume=False,
        mode="answer-eval",
        private_dir=tmp_path / "private",
    )
    assert benchmark.run(args) == 1
    report = json.loads(args.output.read_text())
    assert report["status"] == "partial"
    assert report["contract"]["evaluation"]["output_tokens"] == 800
    before = calls.copy()
    broken = False
    args.resume = True
    assert benchmark.run(args) == 0
    for host, route in [
        ("adapter.test", "/add"),
        ("adapter.test", "/search"),
        ("answer.test", "/v1/chat/completions"),
    ]:
        assert calls[(host, route)] == before[(host, route)]
    report = json.loads(args.output.read_text())
    result = report["questions"]["synthetic-question"]["evaluation"]
    assert result["context_record_indices"] == [0, 1]
    assert result["reader_unmapped_records"] == 0
    assert result["budget_evidence_recall"] == 1
    assert result["local_judge_score"] == 1
