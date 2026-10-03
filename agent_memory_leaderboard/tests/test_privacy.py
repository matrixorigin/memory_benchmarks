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
