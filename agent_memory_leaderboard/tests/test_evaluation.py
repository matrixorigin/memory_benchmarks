import json
import math

import httpx
import pytest

from src import evaluation
from src.evaluation import AnswerEvaluator, EvaluationConfig, ModelError, lexical, parse_verdict


@pytest.fixture
def evaluator(fake_encoding, model_env):
    return AnswerEvaluator(EvaluationConfig.from_env(), "locomo")


def completion(content="prediction", finish="stop", **extra):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}], **extra}


@pytest.mark.parametrize(
    "text,score",
    [
        ('{"label": "CORRECT"}', 1),
        (' {"label": "WRONG"} ', 0),
        ('```json\n{"label": "CORRECT"}\n```', 1),
        ('```\n{"label": "WRONG"}\n```', 0),
    ],
)
def test_verdict_accepts_upstream_label_format(text, score):
    assert parse_verdict(text) == score


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not JSON",
        "[]",
        "true",
    ],
)
def test_verdict_rejects_invalid_or_coerced_scores(text):
    with pytest.raises(ModelError, match="judge_verdict_invalid"):
        parse_verdict(text)


def test_context_keeps_ranked_whole_record_prefix_and_counts_full_request(evaluator):
    question = {
        "question": "When?",
        "question_date": "2025-01-01",
        "options": ["a", "b"],
        "answer": "GOLD MUST NOT ENTER ANSWER PROMPT",
    }
    evidence = ["first", "x" * 3000, "short"]
    messages, _, _ = evaluator.context(question, evidence[:1])
    evaluator.config.input_budget = evaluator.count(messages)
    actual, included, tokens = evaluator.context(question, evidence)
    assert included == 1
    assert tokens == evaluator.count(actual) == evaluator.config.input_budget
    task = json.loads(actual[-1]["content"])
    assert task == {
        "question": "When?",
        "question_date": "2025-01-01",
        "options": ["a", "b"],
        "evidence": ["first"],
    }
    assert "GOLD" not in str(actual)


def test_context_question_alone_over_budget_fails(evaluator):
    evaluator.config.input_budget = 256
    with pytest.raises(ModelError, match="answer_input_budget_exceeded"):
        evaluator.context({"question": "long question"}, [])


def test_context_counts_logarithmic_number_of_full_prefixes(evaluator, fake_encoding):
    contents = [str(i) * 30 for i in range(100)]
    messages, _, _ = evaluator.context({"question": "q"}, contents[:50])
    evaluator.config.input_budget = evaluator.count(messages)
    fake_encoding.calls = 0
    messages, included, tokens = evaluator.context({"question": "q"}, contents)
    assert included == 50
    assert fake_encoding.calls <= 9
    assert tokens == evaluator.count(messages)


@pytest.mark.parametrize("status", [429, 500, 503, 599])
def test_chat_retries_transient_status_with_identical_payload(
    evaluator,
    mock_http,
    monkeypatch,
    status,
):
    requests, sleeps = [], []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(status, text="PRIVATE upstream failure")
        return httpx.Response(200, json=completion())

    mock_http(handler)
    monkeypatch.setattr(evaluation.time, "sleep", sleeps.append)
    assert evaluator.chat("ANSWER", [], 10) == ("prediction", {})
    assert sleeps == [0.5, 1]
    assert len({r.content for r in requests}) == 1
    assert [a["will_retry"] for a in evaluator.model_attempts] == [True, True, False]
    assert "PRIVATE" not in json.dumps(evaluator.model_attempts)
    assert "synthetic-answer-key" not in json.dumps(evaluator.model_attempts)


def test_chat_transport_exhaustion_has_bounded_backoff(evaluator, mock_http, monkeypatch):
    sleeps, requests = [], []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("PRIVATE exception", request=request)

    mock_http(handler)
    monkeypatch.setattr(evaluation.time, "sleep", sleeps.append)
    with pytest.raises(ModelError, match="model_transport_error") as error:
        evaluator.chat("JUDGE", [], 10)
    assert len(requests) == 4
    assert sleeps == [0.5, 1, 2]
    assert not error.value.fatal
    assert "PRIVATE" not in json.dumps(error.value.audit())


def test_chat_429_retry_after_is_capped(evaluator, mock_http, monkeypatch):
    sleeps = []
    mock_http(lambda request: httpx.Response(429, headers={"Retry-After": "9999999999"}))
    monkeypatch.setattr(evaluation.time, "sleep", sleeps.append)
    with pytest.raises(ModelError) as error:
        evaluator.chat("ANSWER", [], 10)
    assert sleeps == [30, 30, 30]
    assert error.value.status == 429


@pytest.mark.parametrize(
    "status,fatal", [(400, False), (401, True), (403, True), (404, True), (405, True), (422, False)]
)
def test_chat_permanent_error_is_not_retried(evaluator, mock_http, monkeypatch, status, fatal):
    mock_http(lambda request: httpx.Response(status, text="PRIVATE body"))
    monkeypatch.setattr(evaluation.time, "sleep", lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(ModelError) as error:
        evaluator.chat("ANSWER", [], 10)
    assert error.value.status == status
    assert error.value.fatal is fatal
    assert len(evaluator.model_attempts) == 1


@pytest.mark.parametrize(
    "body,reason",
    [
        ({}, "model_response_invalid"),
        ({"choices": []}, "model_response_invalid"),
        ([], "model_response_invalid"),
        (completion(finish="length"), "model_completion_incomplete"),
        (completion(finish=[]), "model_completion_incomplete"),
        (completion(content=" "), "model_completion_invalid"),
        (completion(content=[]), "model_completion_invalid"),
    ],
)
def test_completion_rejects_malformed_or_truncated_output(body, reason):
    with pytest.raises(ModelError, match=reason):
        AnswerEvaluator.parse_completion(httpx.Response(200, json=body), "answer")


def test_completion_invalid_json_is_safe():
    with pytest.raises(ModelError, match="model_response_invalid"):
        AnswerEvaluator.parse_completion(httpx.Response(200, text="PRIVATE"), "answer")


def test_completion_allowlists_numeric_usage_only():
    response = httpx.Response(
        200,
        json=completion(
            " answer ",
            usage={
                "prompt_tokens": True,
                "completion_tokens": -1,
                "secret": "PRIVATE",
            },
        ),
    )
    assert AnswerEvaluator.parse_completion(response, "answer") == ("answer", {})


def test_judge_failure_preserves_answer_for_resume(evaluator, tmp_path, mock_http):
    calls = []
    bad_judge = True

    def handler(request):
        calls.append(request.url.host)
        body = json.loads(request.content)
        if request.url.host == "answer.test":
            assert "PRIVATE_GOLD" not in str(body)
            return httpx.Response(200, json=completion("prediction"))
        return httpx.Response(
            200, json=completion("bad JSON" if bad_judge else '```json\n{"label": "CORRECT"}\n```')
        )

    mock_http(handler)
    path = tmp_path / "private" / "answer.json"
    question = {"question": "q", "answer": "PRIVATE_GOLD", "category": 1}
    with pytest.raises(ModelError, match="judge_verdict_invalid") as error:
        evaluator.evaluate(question, ["evidence"], path)
    assert error.value.partial_result["token_f1"] == 0
    assert "local_judge_score" not in error.value.partial_result
    assert json.loads(path.read_text())["prediction"] == "prediction"
    bad_judge = False
    assert evaluator.evaluate(question, ["evidence"], path)["local_judge_score"] == 1
    assert calls == ["answer.test", "judge.test", "judge.test"]
    assert "PRIVATE_GOLD" not in path.read_text()


def test_provider_budget_overflow_keeps_metadata(evaluator, tmp_path, mock_http):
    mock_http(
        lambda request: httpx.Response(
            200, json=completion(usage={"prompt_tokens": evaluator.config.input_budget + 1})
        )
    )
    with pytest.raises(ModelError, match="provider_input_budget_exceeded") as error:
        evaluator.evaluate(
            {"question": "q", "answer": "gold", "category": 1}, [], tmp_path / "answer.json"
        )
    assert error.value.partial_result["context_records"] == 0


@pytest.mark.parametrize("gold", [None, []])
def test_missing_gold_skips_models_even_when_input_would_overflow(
    evaluator,
    tmp_path,
    mock_http,
    gold,
):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, json=completion())

    mock_http(handler)
    evaluator.config.input_budget = 256
    result = evaluator.evaluate({"question": "q", "answer": gold}, [], tmp_path / "answer.json")
    assert result["skipped"] == "missing_gold_answer"
    assert calls == []


@pytest.mark.parametrize(
    "prediction,reference,f1,bleu",
    [
        ("", "", 0, 0),
        ("", "word", 0, 0),
        ("word", "", 0, 0),
        ("a", "b", 0, 0),
        ("Hello!", "hello!", 1, 1),
        ("a a a", "a a", 0.8, 2 / 3),
        ("a", "a b", 2 / 3, math.exp(-1)),
    ],
)
def test_lexical_boundaries(prediction, reference, f1, bleu):
    result = lexical(prediction, reference)
    assert result["token_f1"] == pytest.approx(f1)
    assert result["bleu1"] == pytest.approx(bleu)
