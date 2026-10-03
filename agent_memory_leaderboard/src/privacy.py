"""Job-scoped audit and retention metadata for the AML security policy."""

import hashlib
from datetime import UTC, datetime, timedelta

POLICY_URL = "https://agentmemories.ai/docs#doc-en-security"


def evidence_audit(item):
    """Keep verifiable evidence identity without duplicating retrieved payloads."""
    if "content" not in item:
        return item
    return {key: value for key, value in item.items() if key != "content"} | {
        "content_sha256": hashlib.sha256(item["content"].encode()).hexdigest(),
    }


def apply_policy(report, finished_at=None):
    privacy = report.setdefault("privacy", {})
    privacy.update(
        policy_url=POLICY_URL,
        purpose="current-job-only",
        payload_logging="evidence IDs, hashes and metrics only",
        retention_days=30,
    )
    if finished_at is not None:
        finished_at = finished_at.astimezone(UTC)
        # A failed attempt followed by resume must never extend an already
        # recorded deadline. A fresh job needs a separate UUID scope.
        deadline = finished_at + timedelta(days=30)
        if privacy.get("delete_by"):
            deadline = min(deadline, datetime.fromisoformat(privacy["delete_by"]))
        privacy["finished_at"] = finished_at.isoformat()
        privacy["delete_by"] = deadline.isoformat()
        privacy["cleanup_after"] = (deadline - timedelta(days=1)).isoformat()
