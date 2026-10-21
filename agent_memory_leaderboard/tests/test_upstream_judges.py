"""Synthetic regression coverage; never includes benchmark questions or environment data."""

import hashlib
import json

import httpx
import pytest

from src.evaluation import AnswerEvaluator, EvaluationConfig, ModelError, parse_verdict
from src.judging import LOCOMO_TEMPLATE, PROFILES, judge_requests, model_matches_upstream


def response(text):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        },
    )


def test_pinned_locomo_prompt_bytes():
    assert hashlib.sha256(LOCOMO_TEMPLATE.encode()).hexdigest() == (
        "899d035bd61813bc718812b2997f5908111c63ccd2ed76533e65f5dc3ca337a4"
    )


def test_locomo_long_answer_is_not_truncated_or_wrapped_in_local_rubric():
    question = {"question": "What is the box color?", "category": 1}
    prediction = "The box is blue. It was placed beside the shelf."
    calls = judge_requests("locomo", question, ["Blue", "Azure"], prediction)
    assert len(calls) == 2
    for i, reference in enumerate(["Blue", "Azure"]):
        assert calls[i] == [
            {
                "role": "user",
                "content": LOCOMO_TEMPLATE.format(
                    question=question["question"],
                    gold_answer=reference,
                    generated_answer=prediction,
                ),
            }
        ]


@pytest.mark.parametrize(
    "category,abstention",
    [
        ("single-session-user", False),
        ("single-session-assistant", False),
        ("multi-session", False),
        ("temporal-reasoning", False),
        ("knowledge-update", False),
        ("single-session-preference", False),
        ("abstention", True),
    ],
)
def test_longmemeval_exact_upstream_request(category, abstention):
    # Golden request hashes computed from the pinned upstream function, not this copy.
    hashes = {
        "single-session-user": "f426b14b616e8953f87c788d46b2b78a71da4ae800ec9c20dd6ad7ead41dd107",
        "single-session-assistant": (
            "f426b14b616e8953f87c788d46b2b78a71da4ae800ec9c20dd6ad7ead41dd107"
        ),
        "multi-session": "f426b14b616e8953f87c788d46b2b78a71da4ae800ec9c20dd6ad7ead41dd107",
        "temporal-reasoning": "559f8173aa22b206173149983092a8218e0b97f5a5e09aefca76533c04a4572b",
        "knowledge-update": "c3cd8317e5e69b5727c70fa402ae6e5a439251fec8f91d71cffdd131ca53b93b",
        "single-session-preference": (
            "96a0d4722c035eb92d253984a94ffa2a4f9e2782a6cbc0c61947899f5134e0c1"
        ),
        "abstention": "e7cca41f53913d2620e0efb3ee6d301962d106c1df146508909771e853b7e55b",
    }
    calls = judge_requests(
        "longmemeval", {"question": "Synthetic?", "category": category}, ["reference"], "prediction"
    )
    assert len(calls) == 1 and calls[0][0]["role"] == "user"
    assert hashlib.sha256(calls[0][0]["content"].encode()).hexdigest() == hashes[category]


@pytest.mark.parametrize(
    "text,kind,score",
    [
        ('{"label":"CORRECT"}', "locomo", 1),
        ('```json\n{"label":"WRONG"}\n```', "locomo", 0),
        ('Result: {"label":"correct"}', "locomo", 1),
        ("yes", "longmemeval", 1),
        (" NO\n", "longmemeval", 0),
    ],
)
def test_native_response_formats(text, kind, score):
    assert parse_verdict(text, kind) == score


def test_judge_finish_reason_does_not_override_native_label():
    value, _ = AnswerEvaluator.parse_completion(
        httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"label":"CORRECT"}'}, "finish_reason": "length"}
                ],
            },
        ),
        "judge",
    )
    assert parse_verdict(value) == 1


def test_longmemeval_empty_response_matches_upstream_negative_decision():
    value, _ = AnswerEvaluator.parse_completion(response(""), "judge")
    assert parse_verdict(value, "longmemeval") == 0


@pytest.mark.parametrize(
    "text,kind,score",
    [
        ('{"correct":true}', "locomo", 0),
        ('{"label":"maybe"}', "locomo", 0),
        ('{"label":false}', "locomo", 0),
        ("yes or no", "longmemeval", 1),
        ("unavailable", "longmemeval", 0),
    ],
)
def test_permissive_upstream_label_decisions_are_preserved(text, kind, score):
    assert parse_verdict(text, kind) == score


@pytest.mark.parametrize(
    "labels,score,fails",
    [
        (["WRONG", "CORRECT"], 1, False),
        (["WRONG", "WRONG"], 0, False),
        (["INVALID", "CORRECT"], 1, False),
        (["INVALID", "WRONG"], None, True),
    ],
)
def test_multi_gold_aggregates_like_upstream(
    fake_encoding,
    model_env,
    mock_http,
    tmp_path,
    labels,
    score,
    fails,
):
    judge_calls, events = [], []

    def handler(request):
        body = json.loads(request.content)
        if request.url.host == "answer.test":
            return response("The box is blue.")
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"
        assert "max_tokens" not in body
        assert body["enable_thinking"] is False
        label = labels[len(judge_calls)]
        judge_calls.append(body)
        return response("not JSON" if label == "INVALID" else json.dumps({"label": label}))

    mock_http(handler)
    evaluator = AnswerEvaluator(EvaluationConfig.from_env(), "locomo")
    evaluator.content_observer = lambda phase, value: events.append((phase, value))
    q = {"question": "Box color?", "category": 1, "answer": ["blue", "azure"]}
    if fails:
        with pytest.raises(ModelError) as error:
            evaluator.evaluate(q, ["synthetic evidence"], tmp_path / "answer.json")
        assert len(error.value.partial_result["judge_candidates"]) == 2
        assert "local_judge_score" not in error.value.partial_result
    else:
        result = evaluator.evaluate(q, ["synthetic evidence"], tmp_path / "answer.json")
        assert result["local_judge_score"] == score
        assert len(result["judge_candidates"]) == 2
    assert len(judge_calls) == 2
    assert [v["reference_index"] for phase, v in events if phase == "judge_input"] == [0, 1]


def test_thinking_capability_fallback_is_audited_without_body(
    fake_encoding,
    model_env,
    mock_http,
):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if "enable_thinking" in body:
            return httpx.Response(400, text="enable_thinking unsupported PRIVATE_BODY")
        return response('{"label":"CORRECT"}')

    mock_http(handler)
    evaluator = AnswerEvaluator(EvaluationConfig.from_env(), "locomo")
    verdict, _ = evaluator.chat("JUDGE", [], None, request_options={"enable_thinking": False})
    assert parse_verdict(verdict) == 1
    assert len(calls) == 2 and "enable_thinking" not in calls[1]
    assert evaluator.model_attempts[0]["reason_code"] == "thinking_option_unsupported"
    assert "PRIVATE_BODY" not in json.dumps(evaluator.model_attempts)


def test_longmemeval_http_uses_native_options(fake_encoding, model_env, mock_http, tmp_path):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        if request.url.host == "answer.test":
            return response("Blue")
        calls.append(body)
        return response("yes")

    mock_http(handler)
    evaluator = AnswerEvaluator(EvaluationConfig.from_env(), "longmemeval")
    result = evaluator.evaluate(
        {"question": "Box color?", "category": "single-session-user", "answer": "Blue"},
        ["A blue box."],
        tmp_path / "answer.json",
    )
    assert result["local_judge_score"] == 1
    assert calls[0]["max_tokens"] == 10 and calls[0]["n"] == 1
    assert "enable_thinking" not in calls[0]


def test_model_mismatch_is_visible_in_result_and_warning(
    fake_encoding, model_env, mock_http, tmp_path
):
    mock_http(
        lambda request: response(
            "prediction" if request.url.host == "answer.test" else '{"label":"CORRECT"}'
        )
    )
    with pytest.warns(UserWarning, match="differs from the pinned upstream"):
        evaluator = AnswerEvaluator(EvaluationConfig.from_env(), "locomo")
    result = evaluator.evaluate(
        {"question": "q", "answer": "a", "category": 1}, [], tmp_path / "answer.json"
    )
    assert result["judge_model_matches_upstream"] is False
    assert result["native_evaluator_equivalent"] is False
    assert result["judge_profile"] == PROFILES["locomo"]
    assert model_matches_upstream("locomo", "Qwen/Qwen3-14B")
    assert model_matches_upstream("longmemeval", "gpt-4o-2024-08-06")
