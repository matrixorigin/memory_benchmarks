from datetime import UTC, datetime, timedelta

from src.privacy import apply_policy, evidence_audit


def test_evidence_audit_keeps_identity_without_payload():
    item = {"id": "evidence-1", "content": "private source text", "score": 0.7}
    audit = evidence_audit(item)
    assert "content" not in audit
    assert "private source text" not in str(audit)
    assert audit["id"] == item["id"] and audit["score"] == item["score"]
    assert len(audit["content_sha256"]) == 64
    assert evidence_audit(audit) == audit
    assert item["content"] == "private source text"


def test_resuming_does_not_extend_the_retention_deadline():
    report = {}
    finished = datetime(2026, 10, 3, 12, tzinfo=UTC)
    apply_policy(report, finished)
    deadline = report["privacy"]["delete_by"]
    assert datetime.fromisoformat(deadline) == finished + timedelta(days=30)
    assert datetime.fromisoformat(report["privacy"]["cleanup_after"]) < datetime.fromisoformat(
        deadline
    )
    apply_policy(report, finished + timedelta(days=5))
    assert report["privacy"]["delete_by"] == deadline


def test_audit_save_creates_private_file_before_writing(tmp_path, monkeypatch):
    import os

    from src.benchmark import save

    original_dump = __import__('json').dump

    def inspect_write(value, stream, **kwargs):
        assert os.fstat(stream.fileno()).st_mode & 0o777 == 0o600
        return original_dump(value, stream, **kwargs)

    monkeypatch.setattr('json.dump', inspect_write)
    previous = os.umask(0o022)
    try:
        output = tmp_path / 'new-audit' / 'report.json'
        save(output, {'status': 'completed'})
        assert output.stat().st_mode & 0o777 == 0o600
        assert output.parent.stat().st_mode & 0o777 == 0o700
    finally:
        os.umask(previous)


def test_failed_audit_write_preserves_previous_report(tmp_path):
    import pytest

    from src.benchmark import save

    output = tmp_path / 'report.json'
    save(output, {'status': 'completed'})
    previous = output.read_bytes()
    with pytest.raises(TypeError):
        save(output, {'not_serializable': object()})
    assert output.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [output]


def test_suite_report_is_private_under_permissive_umask(tmp_path, monkeypatch):
    import os

    from src import suite

    output = tmp_path / 'suite' / 'report.json'
    monkeypatch.setattr('sys.argv', ['suite', '--output', str(output)])
    monkeypatch.setattr(suite, 'snapshot', lambda: {'datasets': []})
    previous = os.umask(0o022)
    try:
        suite.main()
        assert output.stat().st_mode & 0o777 == 0o600
        assert output.parent.stat().st_mode & 0o777 == 0o700
    finally:
        os.umask(previous)
