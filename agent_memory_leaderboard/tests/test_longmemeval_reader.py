import hashlib
import json

import httpx
import pytest

from src.embedding_tokens import DEFAULT_TOKEN_BUDGET, token_policy
from src.evaluation import AnswerEvaluator, EvaluationConfig, ModelError
from src.evidence import SourceManifest, source_messages
from src.ingestion import message_parts
from src.models import Message
from src.reading import TEMPLATES, context


@pytest.fixture
def conversation():
    return {
        "session_1_date_time": "2025/01/01 (Wed) 00:00",
        "session_1": [
            {"dia_id": "S1", "role": "user", "speaker": "user", "text": "Box is blue."},
            {"dia_id": "S1", "role": "assistant", "speaker": "assistant", "text": "Noted."},
        ],
        "session_2_date_time": "2025/02/01 (Sat) 00:00",
        "session_2": [
            {"dia_id": "S2", "role": "user", "speaker": "user", "text": "Box is green."},
        ],
    }


def returned(conversation):
    return [
        part
        for _, _, _, body in source_messages(conversation)
        for part, _ in message_parts(Message.model_validate(body), token_budget=7680)
    ]


def config(mode="con", **options):
    return EvaluationConfig(
        "gpt-4o-2024-08-06",
        "http://answer.test/v1",
        "gpt-4o-2024-08-06",
        "http://judge.test/v1",
        longmemeval_reading_method=mode,
        **options,
    )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("con", "3721ae65113e75cbb92e9344598c07ed3698a983d8c7f328982040430321d978"),
        ("direct", "00d532e25c23ae73bc72524a42260797473ade5aced0d22793e7a98f5eb730e9"),
    ],
)
def test_reader_matches_pinned_native_prompt_bytes(conversation, fake_encoding, mode, expected):
    # Goldens are generated from upstream prepare_prompt on this synthetic fixture.
    manifest = SourceManifest(conversation, policy=token_policy(DEFAULT_TOKEN_BUDGET))
    contents = list(reversed(returned(conversation)))
    evaluator = AnswerEvaluator(config(mode), "longmemeval")
    messages, info = context(
        {"question": "Box color?", "question_date": "2025/03/01 (Sat) 00:00"},
        contents,
        manifest.reader_records(contents),
        fake_encoding,
        evaluator.config.input_budget,
        evaluator.config.output_tokens,
        mode,
        evaluator.count,
    )
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert hashlib.sha256(messages[0]["content"].encode()).hexdigest() == expected
    assert info["context_record_indices"] == [0, 1, 2]
    assert info["reader_unmapped_records"] == 0


def test_reader_never_adds_unretrieved_turns_or_answer_labels(conversation, fake_encoding):
    contents = returned(conversation)[:1]
    manifest = SourceManifest(conversation, policy=token_policy(DEFAULT_TOKEN_BUDGET))
    evaluator = AnswerEvaluator(config(), "longmemeval")
    messages, _ = context(
        {"question": "q", "question_date": "date", "answer": "PRIVATE_GOLD"},
        contents,
        manifest.reader_records(contents),
        fake_encoding,
        128000,
        800,
        "con",
        evaluator.count,
    )
    text = messages[0]["content"]
    assert "Box is blue." in text
    assert "Noted." not in text and "Box is green." not in text and "PRIVATE_GOLD" not in text
    assert "Box is blue." not in json.dumps(manifest.dump())


def test_fragment_metadata_does_not_expand_to_whole_source(conversation):
    conversation["session_1"][0]["text"] = "x" * 40000 + "UNRETRIEVED_SUFFIX"
    contents = returned(conversation)
    row = SourceManifest(conversation, policy=token_policy(DEFAULT_TOKEN_BUDGET)).reader_records(
        contents[:1]
    )[0]
    assert row["role"] == "user"
    assert "UNRETRIEVED_SUFFIX" not in row["content"]
    assert len(row["content"]) < 40000


def test_ambiguous_and_unknown_records_are_not_assigned_source_metadata(conversation):
    conversation["session_2_date_time"] = conversation["session_1_date_time"]
    conversation["session_2"][0]["text"] = "Box is blue."
    manifest = SourceManifest(conversation, policy=token_policy(DEFAULT_TOKEN_BUDGET))
    assert manifest.reader_records([returned(conversation)[0], "unknown actual memory"]) == [
        None,
        None,
    ]


def test_truncation_reports_actual_ranks_and_partial_record(fake_encoding):
    evaluator = AnswerEvaluator(config(input_budget=1600, output_tokens=100), "longmemeval")
    contents = ["L" * 1000, "E" * 100]
    metadata = [
        {
            "session": "late",
            "date": "2025-02-01",
            "role": "assistant",
            "content": contents[0],
            "message_index": 0,
            "part_index": 0,
        },
        {
            "session": "early",
            "date": "2025-01-01",
            "role": "user",
            "content": contents[1],
            "message_index": 0,
            "part_index": 0,
        },
    ]
    messages, info = context(
        {"question": "q", "question_date": "date"},
        contents,
        metadata,
        fake_encoding,
        1600,
        100,
        "con",
        evaluator.count,
    )
    assert info["context_record_indices"] == [1]
    assert info["partial_context_record_indices"] == [0]
    assert evaluator.count(messages) <= 1600


def test_unknown_record_content_is_retained_verbatim(fake_encoding):
    evaluator = AnswerEvaluator(config(), "longmemeval")
    messages, info = context(
        {"question": "q", "question_date": "date"},
        ["actual memory"],
        None,
        fake_encoding,
        128000,
        800,
        "con",
        evaluator.count,
    )
    assert '"role": "unknown", "content": "actual memory"' in messages[0]["content"]
    assert info["reader_unmapped_records"] == 1


@pytest.mark.parametrize("mode,tokens", [("con", 800), ("direct", 500)])
def test_native_generation_options_and_successful_answer_reuse(
    fake_encoding,
    model_env,
    mock_http,
    tmp_path,
    mode,
    tokens,
):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.host, body))
        text = "Blue" if request.url.host == "answer.test" else "yes"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": text}, "finish_reason": "stop"},
                ]
            },
        )

    mock_http(handler)
    evaluator = AnswerEvaluator(config(mode), "longmemeval")
    question = {
        "question": "color?",
        "question_date": "date",
        "answer": "Blue",
        "category": "single-session-user",
    }
    for _ in range(2):
        assert (
            evaluator.evaluate(question, ["Blue box"], tmp_path / "answer.json")[
                "local_judge_score"
            ]
            == 1
        )
    answer_calls = [body for host, body in calls if host == "answer.test"]
    assert len(answer_calls) == 1
    assert answer_calls[0]["n"] == 1 and answer_calls[0]["temperature"] == 0
    assert answer_calls[0]["max_tokens"] == tokens
    assert len(answer_calls[0]["messages"]) == 1
    assert evaluator.config.input_budget == 128000


def test_missing_date_fails_before_any_model_request(fake_encoding, model_env, mock_http, tmp_path):
    mock_http(lambda _: pytest.fail("unexpected model request"))
    evaluator = AnswerEvaluator(config(), "longmemeval")
    with pytest.raises(ModelError, match="longmemeval_context_invalid"):
        evaluator.evaluate({"question": "q", "answer": "a"}, [], tmp_path / "answer.json")


def test_unknown_model_requires_explicit_context_limit(fake_encoding, model_env):
    with pytest.raises(ValueError, match="BENCHMARK_INPUT_BUDGET"):
        AnswerEvaluator(EvaluationConfig.from_env(), "longmemeval")


def test_pinned_template_hashes():
    assert hashlib.sha256(TEMPLATES["con"].encode()).hexdigest() == (
        "9e2b3110622929ab896696dd8937231c7436740ec3b9586f653f97346e19ab2c"
    )
