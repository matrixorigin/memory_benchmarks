import json

import httpx
import pytest

from src.diagnostics import RetryAudit, adapter_reason_code


def event():
    return {
        "route": "/add",
        "status": 503,
        "reason": "write_not_searchable",
        "will_retry": True,
        "elapsed_seconds": 1.5,
    }


@pytest.mark.parametrize("tail", ['{"torn":', ""])
def test_audit_repairs_torn_final_append_and_normalizes_missing_newline(tmp_path, tail):
    path = tmp_path / "attempts.jsonl"
    path.write_text(json.dumps(event()) + ("\n" + tail if tail else ""))
    audit = RetryAudit(path)
    audit.observer("hash")(event() | {"status": 200, "will_retry": False})
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == 2
    reopened = RetryAudit(path)
    assert sum(row["attempts"] for row in reopened.summary()) == 2
    assert sum(row["retried_attempts"] for row in reopened.summary()) == 1
    assert sum(row["elapsed_seconds"] for row in reopened.summary()) == 3


def test_audit_does_not_discard_corruption_in_middle(tmp_path):
    path = tmp_path / "attempts.jsonl"
    original = json.dumps(event()) + "\n{broken\n" + json.dumps(event()) + "\n"
    path.write_text(original)
    with pytest.raises(ValueError):
        RetryAudit(path)
    assert path.read_text() == original


def test_new_audit_is_private_and_reloads_totals(tmp_path):
    path = tmp_path / "private" / "attempts.jsonl"
    audit = RetryAudit(path)
    audit.observer("identity-hash")(event())
    assert path.stat().st_mode & 0o777 == 0o600
    assert RetryAudit(path).summary() == audit.summary()


def test_reason_recognition_never_copies_unknown_upstream_text():
    response = httpx.Response(503, json={"detail": {"reason": "PRIVATE arbitrary body"}})
    assert adapter_reason_code(response) is None
