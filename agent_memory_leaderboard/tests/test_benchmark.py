import json
from types import SimpleNamespace

import httpx
import pytest

from src import benchmark
from src.benchmark import (
    adapter_reason_code,
    batches,
    evidence_prefix_scores,
    evidence_score,
    summarize,
)
from src.datasets import normalize


def test_ingestion_keeps_source_order_and_does_not_include_annotations():
    conversation = {
        "session_10": [{"dia_id": "D10:1", "speaker": "A", "text": "Later"}],
        "session_10_date_time": "later date",
        "session_2": [{"dia_id": "D2:1", "speaker": "A", "text": "Earlier",
                       "answer": "NEVER INGEST", "observation": "NEVER INGEST"}],
        "session_2_date_time": "earlier date",
        "session_summary": "NEVER INGEST",
    }
    result = list(batches(conversation))
    assert [r[0] for r in result] == ["session_2", "session_10"]
    assert "NEVER INGEST" not in str(result)
    assert "[dialogue:D2:1] [earlier date] A: Earlier" == result[0][2][0]["content"]


def test_batches_preserve_every_message_at_boundaries():
    turns = [{"dia_id": f"D1:{i}", "speaker": "A", "text": "word " * 110}
             for i in range(41)]
    result = list(batches({"session_1": turns, "session_1_date_time": "date"}))
    messages = [m for _, _, batch in result for m in batch]
    assert len(messages) == 41
    assert all(len(batch) <= 20 for _, _, batch in result)
    assert all(sum(len(m["content"].split()) for m in batch) <= 2000
               for _, _, batch in result)


def test_recall_uses_exact_unique_evidence_and_excludes_unannotated_questions():
    assert evidence_score(["D1:1", "D1:2"], ["[dialogue:D1:1]", "[dialogue:D1:1]"]) == 0.5
    assert evidence_score(["D1:1"], ["[dialogue:D1:10]"]) == 0
    assert evidence_score([], ["[dialogue:D1:1]"]) is None


def test_prefix_recall_preserves_order_unique_gold_and_unlabeled_questions():
    contents = ["[dialogue:D1:1]", "[dialogue:D1:1]", "[dialogue:D2:3]"]
    assert evidence_prefix_scores(["D1:1", "D2:3"], contents, 3) == {"1": 0.5, "3": 1}
    assert evidence_prefix_scores([], contents, 3) == {"1": None, "3": None}
    assert evidence_prefix_scores(["D1:1; D2:3"], contents, 3) == {"1": 0, "3": 0}
    assert evidence_prefix_scores(["D2:3"], contents, 10) == {"1": 0, "5": 1, "10": 1}


@pytest.mark.parametrize("status,reason,code", [
    (503, "Prior write outcome is unknown; retry the same Add later", "write_outcome_unknown"),
    (503, "Memoria write is persisted but not yet searchable; retry Add", "write_not_searchable"),
    (403, "Memoria denied the request; review user scope and content policy",
     "memoria_request_denied"),
    (503, "Memoria connection unavailable", "memoria_connection_unavailable"),
    (403, "Prior write outcome is unknown; retry the same Add later", None),
    (503, "Prior write outcome is unknown; retry the same Add later SECRET", None),
])
def test_failure_reason_codes_require_exact_status_and_reason(status, reason, code):
    response = httpx.Response(status, json={"detail": {"reason": reason, "key": "SECRET"}})
    assert adapter_reason_code(response) == code


@pytest.mark.parametrize("body", [
    [], {"detail": []}, {"detail": {"reason": []}}, {"detail": "SECRET"},
    {"detail": {"reason": "SECRET"}},
])
def test_failure_reason_codes_ignore_unknown_envelopes(body):
    assert adapter_reason_code(httpx.Response(503, json=body)) is None


def test_failure_reason_codes_ignore_invalid_or_oversized_json():
    assert adapter_reason_code(httpx.Response(503, content=b"not JSON SECRET")) is None
    assert adapter_reason_code(httpx.Response(503, json={
        "detail": {"reason": "Prior write outcome is unknown; retry the same Add later"},
        "content": "SECRET" * 2000,
    })) is None


def test_longmemeval_strips_gold_and_preserves_roles_and_history():
    sample = {
        "question_id": "q1", "question_type": "knowledge-update", "question": "Where?",
        "answer": "GOLD", "answer_session_ids": ["s2"],
        "haystack_session_ids": ["s1", "s2"], "haystack_dates": ["day1", "day2"],
        "haystack_sessions": [
            [{"role": "user", "content": "old", "has_answer": False}],
            [{"role": "assistant", "content": "new", "has_answer": True}],
        ],
    }
    converted = normalize([sample], "longmemeval")[0]
    ingested = list(batches(converted["conversation"]))
    assert "GOLD" not in str(ingested) and "has_answer" not in str(ingested)
    assert len(ingested) == 2
    assert ingested[1][2][0]["role"] == "assistant"
    assert converted["qa"][0]["evidence"] == ["s2"]
    sample["question_id"] = "q1_abs"
    assert normalize([sample], "longmemeval")[0]["qa"][0]["evidence"] == []


def test_summary_separates_skips_unannotated_and_empty_retrieval():
    report = {"completed_adds": [], "questions": {
        "a": {"skipped": "multimodal"},
        "b": {"returned": 2, "evidence_recall": None},
        "c": {"returned": 0, "evidence_recall": 0},
        "d": {"returned": 1, "evidence_recall": 1},
    }}
    result = summarize(report)
    assert result["searched_questions"] == 3
    assert result["skipped_questions"] == 1
    assert result["questions_with_evidence"] == 2
    assert result["mean_evidence_recall"] == 0.5
    assert result["empty_results"] == 1


@pytest.mark.parametrize("gold,score,unmatched", [
    (["D1:1"], 1, []),
    (["D1:1; D1:2"], 0, ["D1:1; D1:2"]),
    ([], None, []),
])
def test_failed_ingestion_never_searches_and_resume_reuses_ids(
    tmp_path, monkeypatch, gold, score, unmatched,
):
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps([{
        "sample_id": "sample", "conversation": {
            "session_1_date_time": "date",
            "session_1": [{"dia_id": "D1:1", "speaker": "A", "text": "original fact"}],
        },
        "qa": [{"question": "original query", "answer": "GOLD MUST STAY LOCAL",
                "category": 1, "evidence": gold}],
    }]))
    calls = []
    rejected = True

    def transport(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request.url.path, body))
        assert "GOLD MUST STAY LOCAL" not in str(body)
        if request.url.path == "/add":
            if rejected:
                return httpx.Response(403, json={"detail": {
                    "reason": "Memoria denied the request; review user scope and content policy",
                    "content": "sensitive upstream text",
                }})
            return httpx.Response(200, json={"success": True, **{
                key: body[key] for key in ("request_id", "user_id", "session_id")}})
        if request.url.path == "/search":
            assert body["query"] == "original query"
            return httpx.Response(200, json={"data": [
                {"id": "memory", "content": "[dialogue:D1:1] original fact"}]})
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.Client
    monkeypatch.setattr(benchmark.httpx, "Client",
                        lambda **kwargs: client(transport=httpx.MockTransport(transport), **kwargs))
    args = SimpleNamespace(dataset=source, kind="locomo", conversations=1, top_k=1,
                           adapter_url="http://test", output=tmp_path / "report.json", resume=False)
    assert benchmark.run(args) == 1
    report = json.loads(args.output.read_text())
    assert report["error"]["http_status"] == 403
    assert report["error"]["adapter_reason_code"] == "memoria_request_denied"
    assert report["questions"] == {}
    assert "sensitive upstream text" not in args.output.read_text()
    original_add = next(body for path, body in calls if path == "/add")
    rejected = False
    args.resume = True
    assert benchmark.run(args) == 0
    additions = [body for path, body in calls if path == "/add"]
    assert additions == [original_add, original_add]
    report = json.loads(args.output.read_text())
    assert report["summary"]["mean_evidence_recall"] == score
    question = next(iter(report["questions"].values()))
    assert question["sample_id"] == "sample"
    assert question["retrieved_evidence_ids"] == ["D1:1"]
    assert question["unmatched_gold_evidence"] == unmatched
    assert question["evidence_recall_at_k"] == {"1": score}
    assert report["question_audit_version"] == 1
    assert sum(path == "/search" for path, body in calls) == 1


def test_interrupted_run_records_retention_deadline(tmp_path, monkeypatch):
    source = tmp_path / 'dataset.json'
    source.write_text(json.dumps([{
        'sample_id': 'sample', 'conversation': {
            'session_1_date_time': 'date',
            'session_1': [{'dia_id': 'D1:1', 'speaker': 'A', 'text': 'original fact'}],
        }, 'qa': [],
    }]))
    client = httpx.Client
    monkeypatch.setattr(benchmark.httpx, 'Client', lambda **kwargs: client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)), **kwargs))

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(benchmark, 'call_adapter', interrupt)
    args = SimpleNamespace(dataset=source, kind='locomo', conversations=1, top_k=1,
                           adapter_url='http://test', output=tmp_path / 'report.json', resume=False)
    assert benchmark.run(args) == 1
    report = json.loads(args.output.read_text())
    assert report['status'] == 'interrupted'
    assert report['privacy']['delete_by']
    assert report['privacy']['cleanup_after'] < report['privacy']['delete_by']
