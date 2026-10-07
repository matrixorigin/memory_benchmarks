"""Allowlisted request diagnostics; never record response bodies or exception text."""

import json
import os
from pathlib import Path


def adapter_reason_code(response):
    """Recognize fixed adapter reasons without persisting response text or values."""
    reasons = {
        (503, "Prior write outcome is unknown; retry the same Add later"): "write_outcome_unknown",
        (
            503,
            "Memoria write is persisted but not yet searchable; retry Add",
        ): "write_not_searchable",
        (
            403,
            "Memoria denied the request; review user scope and content policy",
        ): "memoria_request_denied",
        (503, "Memoria connection unavailable"): "memoria_connection_unavailable",
        (503, "Memoria is unavailable; retry the same request later"): "memoria_unavailable",
        (503, "Memoria write is not yet readable"): "write_not_readable",
        (503, "Memoria memory is not active"): "memory_inactive",
        (504, "Memoria request timed out"): "memoria_timeout",
        (504, "Memoria request timed out; retry the same request"): "memoria_timeout",
        (408, "Memoria request timed out; retry the same request"): "memoria_timeout",
        (502, "Memoria transport failed"): "memoria_transport_error",
        (502, "Memoria returned invalid JSON"): "memoria_invalid_json",
        (
            502,
            "Memoria authentication failed; check the backend credential",
        ): "memoria_backend_auth_failed",
        (429, "Memoria capacity exceeded; retry the same request later"): "memoria_capacity",
        (409, "Memoria reported a state conflict"): "memoria_state_conflict",
        (425, "Memoria is not ready; retry the same request later"): "memoria_not_ready",
    }
    if len(response.content) > 8192:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    detail = data.get("detail") if isinstance(data, dict) else None
    reason = detail.get("reason") if isinstance(detail, dict) else None
    if not isinstance(reason, str):
        return None
    return reasons.get((response.status_code, reason))


class RetryAudit:
    def __init__(self, path):
        self.path = Path(path)
        self.totals = {}
        if self.path.exists():
            original = self.path.read_text()
            lines = original.splitlines()
            repaired = False
            for index, line in enumerate(lines):
                try:
                    self.count(json.loads(line))
                except ValueError:
                    if index != len(lines) - 1:
                        raise
                    # Repair only a torn final append before recording more attempts.
                    prefix = "\n".join(lines[:index])
                    self.path.write_text(prefix + ("\n" if index else ""))
                    repaired = True
            if original and not original.endswith("\n") and not repaired:
                with self.path.open("a") as stream:
                    stream.write("\n")

    def count(self, event):
        key = f"{event['route']}:{event['status']}:{event['reason']}"
        group = self.totals.setdefault(
            key,
            {
                "route": event["route"],
                "status": event["status"],
                "reason": event["reason"],
                "attempts": 0,
                "retried_attempts": 0,
                "elapsed_seconds": 0.0,
            },
        )
        group["attempts"] += 1
        group["retried_attempts"] += int(event["will_retry"])
        group["elapsed_seconds"] += event["elapsed_seconds"]

    def observer(self, request_hash):
        def record(event):
            event = event | {"request_sha256": request_hash}
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "a") as stream:
                stream.write(json.dumps(event) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.count(event)

        return record

    def summary(self):
        return sorted(
            self.totals.values(), key=lambda row: (row["route"], str(row["status"]), row["reason"])
        )
