import json
from types import SimpleNamespace

import pytest

from scripts import benchmark_ci as ci


def test_metrics_report_return_count_without_counting_skips():
    result = ci.metrics([
        {"returned": 2, "evidence_recall": 1, "latency_seconds": 1},
        {"returned": 0, "evidence_recall": None, "latency_seconds": 3,
         "unmatched_gold_evidence": ["D1:1; D1:2"]},
        {"skipped": "multimodal"},
    ])
    assert result["searched_questions"] == 2
    assert result["skipped_questions"] == 1
    assert result["mean_returned_records"] == 1
    assert result["questions_with_unmatched_gold"] == 1
    assert result["questions_with_evidence"] == 1
    assert result["mean_evidence_recall"] == 1
    assert ci.metrics([])["mean_returned_records"] is None
    assert ci.metrics([{"returned": 100}])["questions_with_unmatched_gold"] is None
    assert ci.metrics([{"returned": 100}])["evidence_audited_questions"] == 0


def test_history_metrics_keep_unfinished_and_unassigned_records_visible():
    report = {
        "contract": {"conversations": ["first", "second", "unfinished"]},
        "questions": {
            "q1": {"sample_id": "first", "returned": 1, "evidence_recall": 1},
            "q2": {"sample_id": "second", "returned": 0, "evidence_recall": 0},
            "q3": {"sample_id": "second", "skipped": "multimodal"},
            "q4": {"returned": 3, "evidence_recall": 0.5},
        },
    }
    histories, unassigned = ci.history_metrics(report)
    assert histories["first"]["mean_evidence_recall"] == 1
    assert histories["second"]["mean_evidence_recall"] == 0
    assert histories["second"]["skipped_questions"] == 1
    assert histories["unfinished"]["processed_questions"] == 0
    assert histories["unfinished"]["mean_evidence_recall"] is None
    assert unassigned["processed_questions"] == 1
    assert sum(h["processed_questions"] for h in histories.values()) + 1 == 4


@pytest.mark.parametrize("kind,sample,qid", [
    ("locomo", "conv-26", "conv-26#q0001"),
    ("longmemeval", "e47becba", "e47becba"),
])
def test_history_metrics_support_existing_workflow_reports(kind, sample, qid):
    histories, unassigned = ci.history_metrics({
        "contract": {"kind": kind, "conversations": [sample]},
        "questions": {qid: {"returned": 100, "evidence_recall": 1}},
    })
    assert histories[sample]["searched_questions"] == 1
    assert unassigned["processed_questions"] == 0


def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "RESULTS", tmp_path)
    monkeypatch.setattr(ci, "SELECTION", tmp_path / "selection.json")
    monkeypatch.setattr(ci, "REPORT", tmp_path / "run.json")
    monkeypatch.setattr(ci, "DATA", tmp_path / "input.json")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def test_prepare_accepts_blank_label_without_network(tmp_path, monkeypatch):
    workspace(tmp_path, monkeypatch)
    ci.DATA.write_text("[]")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"locomo": {
        "name": "fixture", "histories": 10, "sha256": ci.file_hash(ci.DATA),
    }}))
    monkeypatch.setattr(ci, "CATALOG", catalog)
    for name in ("DATASET", "HISTORIES", "TOP_K", "RUNTIME_LABEL"):
        monkeypatch.delenv("AML_BENCHMARK_" + name, raising=False)
    ci.prepare()
    selection = json.loads(ci.SELECTION.read_text())
    assert selection["status"] == "prepared"
    assert selection["runtime_label"] is None
    assert selection["backend_revision_verified"] is False


@pytest.mark.parametrize("label", [None, "memoria:example-commit"])
def test_execute_uses_label_only_when_provided(tmp_path, monkeypatch, label):
    workspace(tmp_path, monkeypatch)
    ci.DATA.write_text("[]")
    ci.write_json(ci.SELECTION, {
        "status": "prepared", "source": {"sha256": ci.file_hash(ci.DATA)},
        "adapter_url": "https://test.invalid", "dataset": "locomo",
        "histories": 1, "top_k": 100, "runtime_label": label,
    })
    monkeypatch.setenv("ADAPTER_API_KEY", "synthetic-test-key")
    calls = []

    class Ready:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def execute(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ci, "urlopen", lambda *args, **kwargs: Ready())
    monkeypatch.setattr(ci.subprocess, "run", execute)
    assert ci.execute() == 0
    assert ("--runtime-label" in calls[0]) == (label is not None)
    if label is not None:
        assert calls[0][-2:] == ["--runtime-label", label]
    assert "synthetic-test-key" not in ci.SELECTION.read_text()


def test_summary_renders_missing_version_and_history_breakdown(tmp_path, monkeypatch):
    workspace(tmp_path, monkeypatch)
    ci.write_json(ci.SELECTION, {"histories": 1, "top_k": 100, "runtime_label": None})
    ci.write_json(ci.REPORT, {
        "contract": {"kind": "locomo", "conversations": ["conv-26"]},
        "questions": {"conv-26#q0001": {"returned": 100, "evidence_recall": 1}},
        "completed_adds": [], "status": "completed",
    })
    ci.summary()
    result = json.loads((tmp_path / "summary.json").read_text())
    assert result["histories"]["conv-26"]["searched_questions"] == 1
    assert result["metrics"]["mean_returned_records"] == 100
    markdown = (tmp_path / "summary.md").read_text()
    assert "Not recorded" in markdown
    assert "### By history" in markdown
    assert "conv-26" in markdown
