import json

import pytest

from src.checkpoint import checkpoint, load, question_dir, save, save_question


def test_load_recovers_question_sidecars_after_process_stops_before_final_write(tmp_path):
    path = tmp_path / "run.json"
    report = {"checkpoint_format": 2, "status": "running", "questions": {}}
    checkpoint(path, report)
    save_question(path, "question/1", {"evaluation_pending": True})
    save_question(path, "question/2", {"evaluation": {"local_judge_score": 1}})
    assert json.loads(path.read_text())["questions"] == {}
    recovered = load(path)
    assert set(recovered["questions"]) == {"question/1", "question/2"}
    save_question(path, "question/1", {"evaluation_status": "completed"})
    assert load(path)["questions"]["question/1"] == {"evaluation_status": "completed"}
    checkpoint(path, load(path), final=True)
    for sidecar in question_dir(path).glob("*.json"):
        sidecar.unlink()
    assert load(path)["questions"] == recovered["questions"] | {
        "question/1": {"evaluation_status": "completed"}
    }


def test_load_rejects_sidecar_with_wrong_identity(tmp_path):
    path = tmp_path / "run.json"
    checkpoint(path, {"checkpoint_format": 2, "questions": {}})
    save(question_dir(path) / "wrong.json", {"question_id": "q", "record": {}})
    with pytest.raises(ValueError, match="identity"):
        load(path)


def test_failed_atomic_save_preserves_previous_file_and_removes_temporary(tmp_path):
    path = tmp_path / "private" / "run.json"
    save(path, {"version": 1})
    with pytest.raises(TypeError):
        save(path, {"invalid": object()})
    assert json.loads(path.read_text()) == {"version": 1}
    assert list(path.parent.iterdir()) == [path]
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_legacy_report_ignores_unrelated_sidecars(tmp_path):
    path = tmp_path / "run.json"
    save(path, {"questions": {"old": {}}})
    save_question(path, "new", {})
    assert load(path)["questions"] == {"old": {}}
