import gzip
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from src import benchmark
from src.content_diagnostics import (
    FORMAT,
    ContentDiagnostics,
    compare,
    digest,
    read_record,
    signals,
)


def setup_args(tmp_path, *, enabled=True, mode="retrieval"):
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps(
            [
                {
                    "sample_id": "synthetic-history",
                    "conversation": {
                        "session_1_date_time": "2026-01-01",
                        "session_1": [{"dia_id": "D1:1", "speaker": "A", "text": "Atlas uses Go."}],
                    },
                    "qa": [
                        {
                            "id": f"q{i}",
                            "question": f"Atlas language question {i}?",
                            "answer": "Go",
                            "category": 1,
                            "evidence": ["D1:1"],
                        }
                        for i in range(2)
                    ],
                }
            ]
        )
    )
    provenance = tmp_path / "source.json"
    declaration = {
        "version": 1,
        "source_type": "synthetic",
        "source_uri": "synthetic:atlas-demo",
        "source_version": "1",
        "license": "CC0-1.0",
        "dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "permitted_uses": ["local_evaluation", "local_content_diagnostics"],
        "retention_days": 7,
        "independently_obtained": True,
        "no_official_material": True,
        "no_sensitive_content": True,
    }
    provenance.write_text(json.dumps(declaration))
    return SimpleNamespace(
        dataset=source,
        kind="locomo",
        conversations=1,
        top_k=2,
        adapter_url="http://adapter.test",
        output=tmp_path / "run.json",
        resume=False,
        mode=mode,
        private_dir=tmp_path / "private",
        content_diagnostics=enabled,
        diagnostic_source=provenance if enabled else None,
        diagnostic_max_questions=100,
        diagnostic_max_mib=2,
    )


def artifacts(args):
    report = json.loads(args.output.read_text())
    root = args.private_dir / report["run_id"] / "content-diagnostics"
    return report, root, json.loads((root / "manifest.json").read_text())


def handler(calls, *, verdict='{"label": "CORRECT"}', answer="Go", extra_content=""):
    def respond(request):
        body = json.loads(request.content) if request.content else {}
        calls[(request.url.host, request.url.path)] += 1
        if request.url.host == "adapter.test":
            assert "reference_answers" not in str(body)
            if request.url.path == "/add":
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        **{k: body[k] for k in ("request_id", "user_id", "session_id")},
                    },
                )
            if request.url.path == "/search":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "synthetic-memory",
                                "content": "[user]\n[2026-01-01] A: Atlas uses Go." + extra_content,
                                "score": 0.9,
                            }
                        ]
                    },
                )
            return httpx.Response(200)
        content = answer if request.url.host == "answer.test" else verdict
        if request.url.host == "answer.test":
            assert "reference_answers" not in str(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    return respond


def test_opt_in_retrieval_records_actual_requests_shared_history_and_no_models(tmp_path, mock_http):
    args = setup_args(tmp_path)
    calls = Counter()
    mock_http(handler(calls))
    assert benchmark.run(args) == 0
    report, root, state = artifacts(args)
    assert len(list(root.glob("a-*.json.gz"))) == 1
    assert len(list(root.glob("q-*.json.gz"))) == 2
    assert state["summary"]["searched_questions"] == 2
    assert state["incomplete"] == []
    q = read_record(root, "q-" + digest("q0"), 2 * 1024**2)
    assert q["search_request"]["query"] == "Atlas language question 0?"
    assert q["retrieved"][0]["score"] == 0.9
    assert q["metrics"]["evidence_recall"] == 1
    assert "model" not in q
    assert all(host == "adapter.test" for host, path in calls)
    page = (root / ("q-" + digest("q0") + ".html")).read_text()
    assert "Atlas uses Go." in page and "Answer/Eval not run" in page
    assert "source_coverage_unavailable" not in page
    assert report["contract"]["content_diagnostics"]["format"] == FORMAT
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())


def test_default_mode_has_no_content_artifacts(tmp_path, mock_http):
    args = setup_args(tmp_path, enabled=False)
    mock_http(handler(Counter()))
    assert benchmark.run(args) == 0
    assert not args.private_dir.exists()
    assert "content_diagnostics" not in json.loads(args.output.read_text())["contract"]


@pytest.mark.parametrize(
    "change",
    [
        {"source_type": "official"},
        {"source_type": "unknown"},
        {"license": "unknown"},
        {"no_official_material": False},
        {"independently_obtained": False},
        {"no_sensitive_content": False},
        {"permitted_uses": ["local_evaluation"]},
        {"retention_days": 0},
        {"retention_days": 31},
        {"source_type": "public", "source_uri": "https://localhost/data"},
        {"source_type": "public", "source_uri": "https://127.0.0.1/data"},
        {"dataset_sha256": "0" * 64},
    ],
)
def test_invalid_or_official_provenance_rejected_before_requests_or_outputs(
    tmp_path,
    mock_http,
    change,
):
    args = setup_args(tmp_path)
    value = json.loads(args.diagnostic_source.read_text()) | change
    args.diagnostic_source.write_text(json.dumps(value))
    calls = Counter()
    mock_http(handler(calls))
    with pytest.raises(ValueError):
        benchmark.run(args)
    assert not calls and not args.output.exists() and not args.private_dir.exists()


def test_answer_context_verdict_and_failure_resume_are_actual_not_reconstructed(
    tmp_path,
    monkeypatch,
    mock_http,
    model_env,
    fake_encoding,
):
    args = setup_args(tmp_path, mode="answer-eval")
    calls = Counter()
    mock_http(handler(calls, verdict="invalid synthetic verdict"))
    assert benchmark.run(args) == 1
    report, root, state = artifacts(args)
    q = read_record(root, "q-" + digest("q0"), 2 * 1024**2)
    assert q["model"]["answer_input"]["included_records"] == 1
    assert q["model"]["answer_output"]["prediction"] == "Go"
    assert q["model"]["judge_output"]["text"] == "invalid synthetic verdict"
    assert q["model"]["judge_output"]["parse_status"] == "invalid"
    assert signals(q) == ["judge_failed"]
    assert q["metrics"]["evaluation_pending"]
    assert "reference_answers" not in str(q["model"]["answer_input"])
    before = calls.copy()
    args.resume = True
    mock_http(handler(calls))
    assert benchmark.run(args) == 0
    final, root, state2 = artifacts(args)
    assert state2["expires_at"] == state["expires_at"]
    assert calls[("adapter.test", "/add")] == before[("adapter.test", "/add")]
    assert calls[("adapter.test", "/search")] == before[("adapter.test", "/search")]
    assert (
        calls[("answer.test", "/v1/chat/completions")]
        == before[("answer.test", "/v1/chat/completions")]
    )
    q = read_record(root, "q-" + digest("q0"), 2 * 1024**2)
    assert q["model"]["judge_output"]["parse_status"] == "parsed"
    assert q["model"]["judge_output"]["explanation"] == "not provided by judge"
    assert "evaluation_pending" not in q["metrics"]
    assert final["summary"]["evaluation_coverage"] == 1


def test_content_redaction_and_html_escaping_do_not_change_wire_or_hashes(
    tmp_path,
    monkeypatch,
    mock_http,
    model_env,
    fake_encoding,
):
    args = setup_args(tmp_path, mode="answer-eval")
    extra = " synthetic-adapter-key https://private.invalid/config <script>bad()</script>"
    mock_http(handler(Counter(), extra_content=extra))
    assert benchmark.run(args) == 0
    report, root, state = artifacts(args)
    assert state["redactions_present"]
    for p in root.iterdir():
        text = gzip.decompress(p.read_bytes()).decode() if p.suffix == ".gz" else p.read_text()
        assert "synthetic-adapter-key" not in text
        assert "synthetic-answer-key" not in text and "synthetic-judge-key" not in text
        assert "https://private.invalid" not in text
        assert "http://adapter.test" not in text and "http://answer.test" not in text
        if p.suffix == ".html":
            assert "<script>" not in text
    q = read_record(root, "q-" + digest("q0"), 2 * 1024**2)
    raw_content = "[user]\n[2026-01-01] A: Atlas uses Go." + extra
    assert q["retrieved_content_sha256"] == [digest(raw_content)]
    assert "[REDACTED]" in q["retrieved"][0]["content"]
    assert report["questions"]["q0"]["source_attribution"]["unmapped_records"] == 1


def test_limit_omits_content_but_keeps_full_benchmark_denominator(tmp_path, mock_http):
    args = setup_args(tmp_path)
    args.diagnostic_max_questions = 1
    mock_http(handler(Counter(), extra_content="a" * (2 * 1024**2)))
    assert benchmark.run(args) == 0
    report, root, state = artifacts(args)
    assert state["omitted_questions"] == 1
    assert "size_limit_reached" in state["incomplete"]
    assert report["summary"]["searched_questions"] == 2
    assert sum(p.stat().st_size for p in root.iterdir()) <= 2 * 1024**2
    assert "Capture is incomplete" in (root / ("q-" + digest("q0") + ".html")).read_text()


def test_resume_rejects_changed_diagnostics_or_expired_capture(tmp_path, mock_http):
    args = setup_args(tmp_path)
    mock_http(handler(Counter()))
    assert benchmark.run(args) == 0
    report, root, state = artifacts(args)
    args.resume = True
    args.content_diagnostics = False
    args.diagnostic_source = None
    with pytest.raises(ValueError, match="Resume configuration"):
        benchmark.run(args)
    args.content_diagnostics = True
    args.diagnostic_source = tmp_path / "source.json"
    state["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    (root / "manifest.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="Expired diagnostic"):
        benchmark.run(args)


def test_paired_comparison_preserves_missing_and_unscored_denominators(tmp_path, mock_http):
    args = setup_args(tmp_path)
    mock_http(handler(Counter()))
    assert benchmark.run(args) == 0
    _, left, _ = artifacts(args)
    args.output = tmp_path / "second.json"
    args.diagnostic_max_questions = 1
    assert benchmark.run(args) == 0
    _, right, state = artifacts(args)
    compare(left, right, tmp_path / "comparison.html")
    diff = json.loads((tmp_path / "comparison.json").read_text())
    assert len(diff["questions"]) == 2
    assert diff["coverage"][1]["omitted_questions"] == 1
    assert sum(r["after"]["metrics"] is None for r in diff["questions"]) == 1
    state["comparison_contract"]["top_k"] = 1
    (right / "manifest.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="not comparable"):
        compare(left, right, tmp_path / "invalid.html")
    assert not (tmp_path / "invalid.html").exists()


def test_signals_do_not_confuse_source_ambiguity_budget_or_answer_failure():
    base = {"metrics": {"evidence_recall": 0, "source_attribution": {"unmapped_records": 1}}}
    assert signals(base) == ["source_attribution_uncertain"]
    assert "annotated_sources_not_retrieved" in signals({"metrics": {"evidence_recall": 0}})
    assert "annotated_sources_excluded_by_budget" in signals(
        {"metrics": {"evidence_recall": 1, "evaluation": {"budget_evidence_recall": 0}}}
    )
    assert "answer_or_scoring_requires_review" in signals(
        {
            "metrics": {
                "evidence_recall": 1,
                "evaluation": {"budget_evidence_recall": 1, "local_judge_score": 0},
            }
        }
    )


def test_record_reader_rejects_escape_symlink_and_uncompressed_size(tmp_path):
    with pytest.raises(ValueError, match="identity"):
        read_record(tmp_path, "../other", 1024)
    key = "q-" + digest("q0")
    (tmp_path / (key + ".json.gz")).write_bytes(gzip.compress(b"x" * 1025))
    with pytest.raises(ValueError, match="limit"):
        read_record(tmp_path, key, 1024)
    (tmp_path / (key + ".json.gz")).unlink()
    (tmp_path / (key + ".json.gz")).symlink_to(tmp_path / "other")
    with pytest.raises(ValueError, match="symlink"):
        read_record(tmp_path, key, 1024)


@pytest.mark.parametrize(
    "scenario,expected,exit_code",
    [
        ("complete", "no_specific_failure_signal", 0),
        ("missing-evidence", "annotated_sources_not_retrieved", 0),
        ("budget-exclusion", "annotated_sources_excluded_by_budget", 0),
        ("answer-failure", "answer_failed", 1),
        ("judge-failure", "judge_failed", 1),
    ],
)
def test_offline_demo_exercises_real_runner_diagnostic_paths(
    tmp_path, scenario, expected, exit_code
):
    from scripts.content_diagnostics_demo import run

    code, root = run(tmp_path, scenario)
    assert code == exit_code
    record = read_record(root, "q-" + digest("atlas-current-language"), 8 * 1024**2)
    assert expected in signals(record)
    assert "OFFLINE TOOL DEMONSTRATION" in (root / "index.html").read_text()


def test_ci_artifact_upload_uses_metadata_allowlist():
    from pathlib import Path

    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/aml-benchmark.yml"
    text = workflow.read_text().split("- name: Save reports and checkpoints", 1)[1]
    assert "results/run.json" in text
    assert "results/run.questions/*.json" in text
    assert "results/run.evidence/*.json" in text
    assert "results/\n" not in text
    assert ".private" not in text and "content-diagnostics" not in text


def test_export_disk_failure_preserves_final_measurement_checkpoint(
    tmp_path, monkeypatch, mock_http
):
    args = setup_args(tmp_path)
    mock_http(handler(Counter()))

    def fail_export(*args, **kwargs):
        raise OSError("synthetic export disk unavailable")

    monkeypatch.setattr(ContentDiagnostics, "render", fail_export)
    with pytest.raises(OSError):
        benchmark.run(args)
    report = json.loads(args.output.read_text())
    assert report["status"] == "completed"
    assert report["summary"]["searched_questions"] == 2
    assert len(report["questions"]) == 2


def test_explicit_public_source_and_unknown_fields_do_not_copy_private_configuration(
    tmp_path,
    mock_http,
):
    args = setup_args(tmp_path)
    source = json.loads(args.diagnostic_source.read_text())
    source.update(
        source_type="public",
        source_uri="https://example.org/public-dataset/v1",
        ignored_private_configuration="synthetic value that must not be copied",
    )
    args.diagnostic_source.write_text(json.dumps(source))
    mock_http(handler(Counter()))
    assert benchmark.run(args) == 0
    _, root, state = artifacts(args)
    assert state["policy"]["source"]["source_uri"] == source["source_uri"]
    assert "ignored_private_configuration" not in (root / "manifest.json").read_text()


def test_resume_rejects_incompatible_content_format(tmp_path, mock_http):
    args = setup_args(tmp_path)
    mock_http(handler(Counter()))
    assert benchmark.run(args) == 0
    _, root, state = artifacts(args)
    state["format"] = 2
    (root / "manifest.json").write_text(json.dumps(state))
    args.resume = True
    with pytest.raises(ValueError, match="format"):
        benchmark.run(args)


def test_resume_cannot_recreate_manifest_and_extend_retention(tmp_path, mock_http):
    args = setup_args(tmp_path)
    mock_http(handler(Counter()))
    assert benchmark.run(args) == 0
    _, root, _ = artifacts(args)
    (root / "manifest.json").unlink()
    args.resume = True
    with pytest.raises(ValueError, match="Missing diagnostic manifest"):
        benchmark.run(args)
    assert not (root / "manifest.json").exists()


@pytest.mark.parametrize("damage", ["missing_snapshot", "changed_cache"])
def test_pending_resume_discloses_missing_search_and_rejects_cache_mismatch(
    tmp_path,
    mock_http,
    model_env,
    fake_encoding,
    damage,
):
    args = setup_args(tmp_path, mode="answer-eval")
    mock_http(handler(Counter(), verdict="invalid synthetic verdict"))
    assert benchmark.run(args) == 1
    report, root, _ = artifacts(args)
    key = "q-" + digest("q0")
    if damage == "missing_snapshot":
        (root / (key + ".json.gz")).unlink()
    else:
        cache_path = (
            args.private_dir / report["run_id"] / (hashlib.sha256(b"q0").hexdigest() + ".json")
        )
        cache = json.loads(cache_path.read_text())
        cache["contents"] = ["different synthetic evidence"]
        cache_path.write_text(json.dumps(cache))
    args.resume = True
    calls = Counter()
    mock_http(handler(calls))
    assert benchmark.run(args) == (0 if damage == "missing_snapshot" else 1)
    final, _, state = artifacts(args)
    assert calls[("adapter.test", "/search")] == 0
    if damage == "missing_snapshot":
        assert "resumed_search_snapshot_unavailable" in state["incomplete"]
        q = read_record(root, key, 2 * 1024**2)
        assert "retrieved" not in q
        assert q["model"]["answer_output"]["from_signature_verified_cache"]
    else:
        assert final["error"]["type"] == "ValueError"
        assert sum(n for (_, path), n in calls.items() if path != "/health") == 0
