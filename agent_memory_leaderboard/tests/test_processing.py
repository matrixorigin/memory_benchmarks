import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from src.config import Settings
from src.errors import AdapterError
from src.memoria import Memoria, Memory
from src.processing.__main__ import load, main, output_lock, save
from src.processing.experiment import publish, search
from src.processing.extract import Extractor, ProcessingError, ProcessorConfig
from src.processing.models import Candidate, History, Snapshot, digest
from src.processing.prompt import messages
from src.processing.validation import validate_candidate


def source(source_id="s1", content="昨天我去了上海。", **overrides):
    return {
        "source_id": source_id,
        "source_version": "v1",
        "conversation_id": "c1",
        "speaker": "Person A",
        "role": "user",
        "content": content,
        "message_time": "2026-10-10T09:00:00+08:00",
        "timezone": "Asia/Shanghai",
        **overrides,
    }


def history(*sources):
    return History.model_validate(
        {
            "data_policy": "synthetic",
            "user_id": "synthetic-user",
            "messages": list(sources) or [source()],
        }
    )


def candidate(**overrides):
    return {
        "text": "人物 A 于 2026-10-09 到访上海。",
        "kind": "event",
        "subjects": ["Person A"],
        "assertion": "explicit",
        "event_status": "completed",
        "event_time": {
            "original_expression": "昨天",
            "start": "2026-10-09",
            "end": None,
            "precision": "day",
            "timezone": None,
            "anchor_source_ids": ["s1"],
        },
        "evidence": [{"source_id": "s1", "quote": "昨天我去了上海。"}],
        "uncertainties": [],
        **overrides,
    }


def completion(items=None, *, finish="stop", pending=None):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "content": json.dumps(
                        {
                            "memories": [candidate()] if items is None else items,
                            "unprocessed_source_ids": pending or [],
                        }
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


class Encoder:
    def encode(self, text, **_):
        return list(text.encode())  # Conservative deterministic byte budget for unit tests.


def extractor(handler=None, **overrides):
    config = ProcessorConfig(
        "synthetic-model",
        "https://model.test/v1",
        "synthetic-secret",
        input_tokens=16000,
        **overrides,
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler or (lambda _: httpx.Response(200, json=completion())))
    )
    return Extractor(config, client, Encoder())


def snapshot():
    return extractor().run(history(), enabled=True)


class Backend:
    """Idempotent synthetic store exercising ownership, partial writes and queries."""

    def __init__(self):
        self.settings = SimpleNamespace(memoria_url="https://store.test", namespace="synthetic")
        self.items, self.writes, self.searches = {}, [], []
        self.failed_content = None
        self.failed_owner = None

    def store_idempotent(self, owner, body, marker):
        self.writes.append((owner, body, marker))
        if body["content"] == self.failed_content:
            raise AdapterError(503, "SYNTHETIC_PRIVATE_FAILURE")
        key = (owner, marker)
        self.items.setdefault(
            key,
            Memory(
                memory_id=digest(key),
                user_id=owner,
                content=body["content"],
                is_active=True,
                extra_metadata=body["extra_metadata"],
            ),
        )
        return self.items[key]

    def wait_for_visibility(self, owner, memory_id, marker):
        assert self.items[(owner, marker)].memory_id == memory_id
        return self.items[(owner, marker)]

    def search(self, owner, query, top_k):
        self.searches.append(owner)
        if owner == self.failed_owner:
            raise AdapterError(503, "SYNTHETIC_PRIVATE_FAILURE")
        return [value for (scope, _), value in self.items.items() if scope == owner][:top_k]


def test_history_rejects_questions_answers_and_duplicate_ids():
    data = history().model_dump()
    for field in ("question", "answer", "judge", "owner", "branch"):
        with pytest.raises(ValidationError):
            History.model_validate(data | {field: "must not enter processing"})
    data["messages"] *= 2
    with pytest.raises(ValidationError):
        History.model_validate(data)
    with pytest.raises(ValidationError):
        History.model_validate(history().model_dump() | {"data_policy": "official"})


def test_prompt_preserves_speaker_sources_time_and_never_reads_evaluation():
    request = messages(history().messages)
    assert "synthetic-secret" not in json.dumps(request)
    assert "2026-10-10" in request[1]["content"]
    assert "Person A" in request[1]["content"]
    assert '"source_version": "v1"' in request[1]["content"]
    assert "Message role is not a person's" in request[0]["content"]


def test_default_off_makes_no_calls_or_reads(tmp_path, capsys):
    def forbidden(_):
        pytest.fail("unexpected request")

    assert extractor(forbidden).run(history()) is None
    assert publish(snapshot(), None, "synthetic") is None
    code = main(
        ["extract", "--input", str(tmp_path / "missing"), "--output", str(tmp_path / "out")]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "disabled"
    assert not (tmp_path / "out").exists()


def test_valid_date_preserves_candidate():
    item = Candidate.model_validate(candidate())
    assert validate_candidate(item, history().messages) == item


@pytest.mark.parametrize("value", ["2026-10-08", "2026-02-30", None])
def test_bad_date_removes_generated_date_from_text_too(value):
    item = candidate()
    item["event_time"]["start"] = value
    if value is None:
        item["event_time"]["end"] = "2026-10-08"
    result = validate_candidate(Candidate.model_validate(item), history().messages)
    assert result.event_time.start is None
    assert result.text == "昨天我去了上海。"
    assert result.uncertainties == ["time_normalization_unverified"]


@pytest.mark.parametrize(
    "expression,stamp,result,precision",
    [
        ("yesterday", "2024-03-01", "2024-02-29", "day"),
        ("tomorrow", "2025-12-31", "2026-01-01", "day"),
        ("下个月", "2026-12-10", "2027-01", "month"),
        ("2 days ago", "2026-01-01", "2025-12-30", "day"),
    ],
)
def test_deterministic_calendar_boundaries(expression, stamp, result, precision):
    item = candidate(text="quoted event", evidence=[{"source_id": "s1", "quote": expression}])
    item["event_time"].update(original_expression=expression, start=result, precision=precision)
    sources = history(source(content=expression, message_time=stamp)).messages
    assert validate_candidate(Candidate.model_validate(item), sources).event_time.start == result


@pytest.mark.parametrize("mutation", ["unknown_source", "bad_quote", "anchor", "time_quote"])
def test_invalid_evidence_is_rejected(mutation):
    item = candidate()
    if mutation == "unknown_source":
        item["evidence"][0]["source_id"] = "another-owner-source"
    elif mutation == "bad_quote":
        item["evidence"][0]["quote"] = "invented statement"
    elif mutation == "anchor":
        item["event_time"]["anchor_source_ids"] = ["uncited"]
    else:
        item["event_time"]["original_expression"] = "tomorrow"
    with pytest.raises(ValueError):
        validate_candidate(Candidate.model_validate(item), history().messages)


def test_missing_anchor_and_ambiguous_date_keep_original_expression():
    item = candidate()
    for sources in (
        history(source(message_time=None)).messages,
        history(source(content="last Friday I travelled")).messages,
    ):
        current = deepcopy(item)
        if sources[0].message_time:
            current["event_time"]["original_expression"] = "last Friday"
            current["evidence"][0]["quote"] = sources[0].content
        result = validate_candidate(Candidate.model_validate(current), sources)
        assert result.event_time.start is None
        assert result.text == sources[0].content


def test_relative_date_cannot_use_another_cited_messages_timestamp():
    item = candidate()
    item["event_time"]["anchor_source_ids"] = ["s2"]
    item["event_time"]["start"] = "2026-10-19"
    item["evidence"].append({"source_id": "s2", "quote": "Other context"})
    sources = history(source(), source("s2", "Other context", message_time="2026-10-20")).messages
    result = validate_candidate(Candidate.model_validate(item), sources)
    assert result.event_time.start is None
    assert "2026-10-19" not in result.text


def test_windows_are_bounded_overlap_and_never_cross_conversations():
    task = extractor(window_messages=3, overlap_messages=1)
    h = history(*[source(f"s{i}", "a", conversation_id="c1" if i < 5 else "c2") for i in range(7)])
    windows = list(task.windows(h))
    assert [[s.source_id for s in w] for w in windows] == [
        ["s0", "s1", "s2"],
        ["s2", "s3", "s4"],
        ["s5", "s6"],
    ]


def test_failure_is_recorded_and_later_windows_continue():
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(503, text="PRIVATE BODY")
            if len(calls) == 1
            else httpx.Response(200, json=completion([]))
        )

    result = extractor(handler, window_messages=1, overlap_messages=0).run(
        history(source(), source("s2", "another fact")), enabled=True
    )
    assert [r.status for r in result.results] == ["failed", "complete"]
    assert "PRIVATE" not in result.model_dump_json()
    assert "synthetic-secret" not in result.model_dump_json()


@pytest.mark.parametrize("finish", ["length", "content_filter", None])
def test_truncated_model_response_is_never_published(finish):
    result = extractor(lambda _: httpx.Response(200, json=completion(finish=finish))).run(
        history(), enabled=True
    )
    assert result.results[0].reason == "model_completion_incomplete"
    assert result.memories() == {}


def test_one_invalid_candidate_keeps_valid_candidates_but_marks_partial():
    bad = candidate(evidence=[{"source_id": "outside", "quote": "x"}])
    result = extractor(lambda _: httpx.Response(200, json=completion([candidate(), bad]))).run(
        history(), enabled=True
    )
    assert result.results[0].status == "partial"
    assert len(result.memories()) == 1
    assert not result.complete


def test_unprocessed_sources_block_completion():
    result = extractor(lambda _: httpx.Response(200, json=completion([], pending=["s1"]))).run(
        history(), enabled=True
    )
    assert result.results[0].status == "partial"
    assert result.results[0].unprocessed_source_ids == ["s1"]


def test_oversized_message_fails_without_model_but_later_messages_run():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion([]))

    result = extractor(handler, window_messages=1, overlap_messages=0).run(
        history(source(content="x" * 20000), source("s2", "short")), enabled=True
    )
    assert len(calls) == 1
    assert result.results[0].reason == "input_budget_exceeded"
    assert result.results[1].status == "complete"


def test_resume_reuses_complete_windows_and_rejects_changed_contract():
    prior = snapshot()

    def forbidden(_):
        pytest.fail("model replayed a completed window")

    task = extractor(forbidden)
    assert task.run(history(), enabled=True, previous=prior) == prior
    with pytest.raises(ProcessingError, match="resume_contract_mismatch"):
        task.run(history(source(source_version="v2")), enabled=True, previous=prior)
    with pytest.raises(ProcessingError, match="resume_contract_mismatch"):
        extractor(forbidden, output_tokens=1024).run(history(), enabled=True, previous=prior)


def test_resume_keeps_failure_history_and_future_completed_work():
    calls = []

    def failing_first(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=completion([]))

    task = extractor(failing_first, window_messages=1, overlap_messages=0)
    h = history(source(), source("s2", "second message"))
    prior = task.run(h, enabled=True)
    assert prior.results[0].http_status == 503
    resumed = extractor(
        lambda _: httpx.Response(200, json=completion([])),
        window_messages=1,
        overlap_messages=0,
    )
    checkpoints = []
    result = resumed.run(
        h,
        enabled=True,
        previous=prior,
        checkpoint=lambda s: checkpoints.append(s.model_copy(deep=True)),
    )
    assert len(checkpoints[0].results) == 2
    assert result.complete
    assert [a.status for a in result.results[0].attempts] == ["failed", "complete"]
    assert len(result.results[1].attempts) == 1


def test_model_transport_and_malformed_json_are_recorded_without_raw_errors():
    def transport(request):
        raise httpx.ReadTimeout("PRIVATE FAILURE", request=request)

    result = extractor(transport).run(history(), enabled=True)
    assert result.results[0].reason == "model_transport_error"
    assert "PRIVATE" not in result.model_dump_json()
    malformed = completion()
    malformed["choices"][0]["message"]["content"] = '{"memories":[],"memories":[]}'
    result = extractor(lambda _: httpx.Response(200, json=malformed)).run(history(), enabled=True)
    assert result.results[0].reason == "model_output_invalid"


def test_changed_source_scope_is_rejected_before_publication():
    task = snapshot()
    task.results[0].memories[0].evidence[0].source_id = "outside-history"
    backend = Backend()
    with pytest.raises(ProcessingError, match="snapshot_candidate_invalid"):
        publish(task, backend, "test", enabled=True)
    assert backend.writes == []


def test_publish_uses_isolated_owners_and_idempotent_replay():
    backend = Backend()
    task = snapshot()
    result = publish(task, backend, "test", enabled=True)
    assert result.raw_ready and result.derived_ready
    assert result.raw_owner != result.derived_owner
    assert len(backend.items) == 2
    second = publish(task, backend, "test", enabled=True)
    assert second == result
    assert len(backend.items) == 2
    raw, derived = backend.writes[:2]
    assert raw[1]["memory_type"] == "episodic"
    assert derived[1]["extra_metadata"]["offline_processing"]["source_versions"] == {"s1": "v1"}
    assert raw[1]["observed_at"] == derived[1]["observed_at"]
    other = publish(task, backend, "other-test", enabled=True)
    assert other.raw_owner != result.raw_owner
    changed = task.model_copy(deep=True)
    changed.results[0].memories[0].text = "different wording of the same visit"
    third = publish(changed, backend, "test", enabled=True)
    assert third.raw_owner == result.raw_owner
    assert third.derived_owner != result.derived_owner


def test_publication_resume_skips_confirmed_writes():
    backend = Backend()
    task = snapshot()
    prior = publish(task, backend, "test", enabled=True)
    backend.writes.clear()
    assert publish(task, backend, "test", enabled=True, previous=prior) == prior
    assert backend.writes == []


def test_frozen_baseline_mode_only_calls_derived_and_records_partial_coverage():
    backend = Backend()
    task = snapshot()
    task.results[0].status = "partial"
    task.results[0].reason = "candidate_validation_failed"
    p = publish(task, backend, "test", enabled=True, derived_only=True)
    assert not p.raw
    assert all(owner == p.derived_owner for owner, _, _ in backend.writes)
    baseline = {"data": [{"id": "old-1", "content": "unchanged original evidence"}]}
    result = search(p, backend, "q", baseline=baseline, encoder=Encoder())
    assert result["data"][0]["content"] == baseline["data"][0]["content"]
    assert not backend.searches
    combined = search(
        p,
        backend,
        "q",
        baseline=baseline,
        enable_derived=True,
        allow_partial_extraction=True,
        encoder=Encoder(),
    )
    assert combined["derived_status"] == "partial_extraction"
    assert len(combined["data"]) == 2
    assert backend.searches == [p.derived_owner]
    assert combined["baseline_sha256"] == result["baseline_sha256"]


def test_publish_failure_continues_and_is_never_ready():
    backend = Backend()
    backend.failed_content = (
        "[user; speaker=Person A; message_time=2026-10-10T09:00:00+08:00; "
        "timezone=Asia/Shanghai]\n昨天我去了上海。"
    )
    saved = []
    result = publish(
        snapshot(), backend, "test", enabled=True, checkpoint=lambda p: saved.append(p.model_dump())
    )
    assert len(saved[0]["raw"]) == len(saved[0]["derived"]) == 1
    assert saved[0]["derived"][0]["status"] == "pending"
    assert not result.raw_ready
    assert result.derived[0].status == "ready"
    assert "SYNTHETIC_PRIVATE_FAILURE" not in result.model_dump_json()
    with pytest.raises(ProcessingError, match="raw_publication_incomplete"):
        search(result, backend, "query", encoder=Encoder())


def test_interrupted_extraction_prefix_is_not_derived_ready():
    task = snapshot()
    task.history.messages.append(history(source("s2", "unprocessed")).messages[0])
    result = publish(task, Backend(), "test", enabled=True)
    assert result.raw_ready
    assert not result.derived_ready


def test_raw_only_never_queries_derived_and_fallback_matches_baseline():
    backend = Backend()
    p = publish(snapshot(), backend, "test", enabled=True)
    baseline = search(p, backend, "visit", top_k=10, encoder=Encoder())
    assert backend.searches == [p.raw_owner]
    assert baseline["derived_status"] == "disabled"
    combined = search(p, backend, "visit", enable_derived=True, top_k=10, encoder=Encoder())
    assert [i["channel"] for i in combined["data"]] == ["raw", "derived"]
    backend.failed_owner = p.derived_owner
    fallback = search(p, backend, "visit", enable_derived=True, top_k=10, encoder=Encoder())
    assert fallback["data"] == baseline["data"]
    assert fallback["derived_status"] == "failed_raw_fallback"
    p.extraction_complete = False
    backend.searches.clear()
    result = search(p, backend, "visit", enable_derived=True, encoder=Encoder())
    assert result["derived_status"] == "not_ready"
    assert backend.searches == [p.raw_owner]


def test_search_rejects_wrong_backend_or_manifest_scope():
    backend = Backend()
    p = publish(snapshot(), backend, "test", enabled=True)
    p.raw_owner = "different-owner"
    with pytest.raises(ProcessingError, match="publication_scope_mismatch"):
        search(p, backend, "q", encoder=Encoder())
    assert not backend.searches
    p.backend_fingerprint = "wrong"
    with pytest.raises(ProcessingError, match="publication_backend_mismatch"):
        search(p, backend, "q", encoder=Encoder())


def test_derived_unexpected_record_falls_back_raw():
    backend = Backend()
    p = publish(snapshot(), backend, "test", enabled=True)
    backend.items[(p.derived_owner, "extra")] = Memory(
        memory_id="unexpected", user_id=p.derived_owner, content="other snapshot", is_active=True
    )
    result = search(p, backend, "q", enable_derived=True, encoder=Encoder())
    assert result["derived_status"] == "failed_raw_fallback"
    assert all(i["channel"] == "raw" for i in result["data"])


def test_search_top_k_and_token_budget_are_shared_and_bounded():
    backend = Backend()
    p = publish(snapshot(), backend, "test", enabled=True)
    one = search(p, backend, "q", enable_derived=True, top_k=1, encoder=Encoder())
    assert len(one["data"]) == 1 and one["data"][0]["channel"] == "raw"
    limited = search(p, backend, "q", enable_derived=True, token_budget=256, encoder=Encoder())
    assert limited["data"] == []
    for kwargs in ({"top_k": 0}, {"top_k": 101}, {"token_budget": 255}):
        with pytest.raises(ProcessingError):
            search(p, backend, "q", encoder=Encoder(), **kwargs)


def test_private_utf8_artifacts_and_nonblocking_output_lock(tmp_path):
    path = tmp_path / "snapshot.json"
    save(path, snapshot().model_dump())
    assert path.stat().st_mode & 0o777 == 0o600
    assert "昨天" in path.read_text()
    assert load(path, Snapshot).complete
    with output_lock(path):
        with pytest.raises(ProcessingError, match="output_in_use"):
            with output_lock(path):
                pytest.fail("second writer entered")


def test_cli_validation_errors_do_not_echo_payloads(tmp_path, capsys):
    source_path = tmp_path / "private-input.json"
    source_path.write_text('{"PRIVATE_CONTENT": "PRIVATE_SECRET"}')
    assert (
        main(
            [
                "extract",
                "--input",
                str(source_path),
                "--output",
                str(tmp_path / "out"),
                "--enable-processing",
            ]
        )
        == 2
    )
    printed = capsys.readouterr().out
    assert "PRIVATE" not in printed
    assert str(tmp_path) not in printed


def test_http_boundary_end_to_end_publish_and_retrieve():
    stored = {}

    def handler(request):
        owner = request.headers["X-User-Id"]
        if request.method == "GET":
            mid = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(
                200,
                json=next(
                    v for (u, _), v in stored.items() if u == owner and v["memory_id"] == mid
                ),
            )
        body = json.loads(request.content)
        assert body["branch"] == "main"
        if request.url.path.endswith("/idempotent"):
            key = (owner, body["idempotency_key"])
            stored.setdefault(
                key,
                {
                    "memory_id": digest(key),
                    "user_id": owner,
                    "is_active": True,
                    "content": body["content"],
                    "extra_metadata": body["extra_metadata"],
                },
            )
            return httpx.Response(200, json=stored[key])
        if request.url.path.endswith("/search"):
            rows = [v for (u, _), v in stored.items() if u == owner]
            if "subject_id" in body:
                rows = [v for v in rows if v["extra_metadata"]["aml_write"] == body["subject_id"]]
            return httpx.Response(200, json=rows[: body["top_k"]])
        pytest.fail("unexpected route")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        backend = Memoria(Settings("https://store.test", "synthetic-master"), client)
        publication = publish(snapshot(), backend, "http-test", enabled=True)
        result = search(
            publication, backend, "When was the visit?", enable_derived=True, encoder=Encoder()
        )
    assert publication.raw_ready and publication.derived_ready
    assert len(result["data"]) == 2
    assert result["data"][1]["source_ids"] == ["s1"]
    assert "message_time=2026-10-10" in result["data"][1]["content"]
