"""Publish immutable offline snapshots and query physically isolated candidates."""

import json
import math
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from ..errors import AdapterError
from ..ingestion import MAX_MEMORY_BYTES, split_utf8
from .extract import ProcessingError, validate_snapshot
from .models import StrictModel, digest


class PublishedRecord(StrictModel):
    marker: str
    content_sha256: str
    source_ids: list[str]
    memory_id: str | None = None
    status: Literal["pending", "ready", "failed"] = "pending"
    reason: str | None = None
    http_status: int | None = None


class Publication(StrictModel):
    format_version: Literal[1] = 1
    experiment_hash: str
    history_hash: str
    snapshot_hash: str
    backend_fingerprint: str
    raw_owner: str
    derived_owner: str
    extraction_complete: bool
    raw: list[PublishedRecord] = Field(default_factory=list)
    derived: list[PublishedRecord] = Field(default_factory=list)

    @property
    def raw_ready(self):
        return bool(self.raw) and all(r.status == "ready" and r.memory_id for r in self.raw)

    @property
    def derived_ready(self):
        return self.extraction_complete and all(
            r.status == "ready" and r.memory_id for r in self.derived
        )


def backend_fingerprint(memoria):
    return digest([memoria.settings.memoria_url.rstrip("/"), memoria.settings.namespace])


def owners(experiment_hash, history_hash, snapshot_hash, backend):
    scope = ["offline-memory-experiment-v1", backend, experiment_hash, history_hash]
    return digest([*scope, "raw"]), digest([*scope, "derived", snapshot_hash])


def observed_at(sources):
    stamps = []
    for source in sources:
        try:
            stamp = datetime.fromisoformat(source.message_time or "")
            if stamp.tzinfo is not None:
                stamps.append(stamp.astimezone(UTC))
        except (ValueError, OverflowError):
            pass
    return max(stamps).isoformat() if stamps else None


def source_content(source):
    header = f"[{source.role}; speaker={source.speaker or 'unknown'}"
    if source.message_time is not None:
        header += f"; message_time={source.message_time}"
    if source.timezone is not None:
        header += f"; timezone={source.timezone}"
    return header + "]\n", source.content


def derived_content(memory, sources):
    event = memory.event_time
    parts = [
        f"[Derived {memory.kind}; assertion={memory.assertion}; status={memory.event_status}]",
        memory.text,
    ]
    if event.start is not None:
        parts.append(f"Calendar-checked time ({event.precision}): {event.start}")
    if memory.uncertainties:
        parts.append("Uncertainties: " + "; ".join(memory.uncertainties))
    parts.append("Source excerpts:")
    parts.extend(
        f"[Source {e.source_id}]\n{source_content(sources[e.source_id])[0]}{e.quote}"
        for e in memory.evidence
    )
    return "\n".join(parts)


def publish(snapshot, memoria, experiment_id, *, enabled=False, checkpoint=None):
    if not enabled:
        return None
    validate_snapshot(snapshot)
    if not experiment_id.strip() or len(experiment_id) > 256:
        raise ProcessingError("experiment_id_invalid")
    history_hash = digest(snapshot.history.model_dump())
    # Metrics and retry durations do not change semantic snapshot identity.
    snapshot_hash = digest(
        {
            "history": history_hash,
            "contract": snapshot.contract,
            "results": [
                r.model_dump(exclude={"usage", "elapsed_seconds", "attempts"})
                for r in snapshot.results
            ],
        }
    )
    backend = backend_fingerprint(memoria)
    experiment_hash = digest(experiment_id)
    raw_owner, derived_owner = owners(experiment_hash, history_hash, snapshot_hash, backend)
    publication = Publication(
        experiment_hash=experiment_hash,
        history_hash=history_hash,
        snapshot_hash=snapshot_hash,
        backend_fingerprint=backend,
        raw_owner=raw_owner,
        derived_owner=derived_owner,
        extraction_complete=snapshot.complete,
    )
    sources = {s.source_id: s for s in snapshot.history.messages}
    plan = []

    def add(channel, content, source_ids, identity, details):
        owner = raw_owner if channel == "raw" else derived_owner
        refs = [sources[sid] for sid in source_ids]
        marker = digest([owner, identity, content, details])
        record = PublishedRecord(
            marker=marker,
            content_sha256=digest(content),
            source_ids=source_ids,
        )
        getattr(publication, channel).append(record)
        body = {
            "content": content,
            "memory_type": "episodic" if channel == "raw" else details["kind"],
            # Isolation is by owner, never by overloading memory_type.
            "subject_id": marker,
            "session_id": digest(sorted({s.conversation_id for s in refs})),
            "extra_metadata": {
                "aml_write": marker,
                "offline_processing": {
                    "experiment_hash": experiment_hash,
                    "channel": channel,
                    "history_hash": history_hash,
                    "source_versions": {s.source_id: s.source_version for s in refs},
                    **details,
                },
            },
        }
        if stamp := observed_at(refs):
            body["observed_at"] = stamp
        plan.append((owner, body, record))

    for source in sources.values():
        header, content = source_content(source)
        for part, text in enumerate(split_utf8(content, MAX_MEMORY_BYTES - len(header.encode()))):
            add("raw", header + text, [source.source_id], [source.source_id, part], {})
    for identity, memory in snapshot.memories().items():
        source_ids = sorted({e.source_id for e in memory.evidence})
        add(
            "derived",
            derived_content(memory, sources),
            source_ids,
            identity,
            {
                "kind": "semantic" if memory.kind == "fact" else "episodic",
                "snapshot_hash": snapshot_hash,
                "processor_contract": snapshot.contract,
                "candidate": memory.model_dump(),
            },
        )
    # Save a full pending manifest first: a mid-publication interruption cannot
    # masquerade as a complete, shorter candidate set.
    if checkpoint:
        checkpoint(publication)
    for owner, body, record in plan:
        try:
            if len(body["content"].encode()) > MAX_MEMORY_BYTES:
                raise ProcessingError("derived_content_too_large")
            memory = memoria.store_idempotent(owner, body, record.marker)
            persisted = memoria.wait_for_visibility(owner, memory.memory_id, record.marker)
            if digest(persisted.content) != record.content_sha256:
                raise ProcessingError("stored_content_mismatch")
            record.memory_id = persisted.memory_id
            record.status = "ready"
        except (AdapterError, ProcessingError) as exc:
            # Idempotent endpoint + same immutable plan makes explicit reruns safe.
            record.status, record.reason = "failed", "publication_failed"
            if isinstance(exc, ProcessingError):
                record.reason = str(exc)
            else:
                record.http_status = exc.status
        if checkpoint:
            checkpoint(publication)
    return publication


def search(
    publication,
    memoria,
    query,
    *,
    enable_derived=False,
    top_k=100,
    token_budget=8192,
    encoding="o200k_base",
    encoder=None,
):
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ProcessingError("top_k_invalid")
    if type(token_budget) is not int or not 256 <= token_budget <= 128000:
        raise ProcessingError("token_budget_invalid")
    if not isinstance(query, str) or not query.strip() or len(query) > 100000:
        raise ProcessingError("query_invalid")
    if publication.backend_fingerprint != backend_fingerprint(memoria):
        raise ProcessingError("publication_backend_mismatch")
    expected = owners(
        publication.experiment_hash,
        publication.history_hash,
        publication.snapshot_hash,
        publication.backend_fingerprint,
    )
    if expected != (publication.raw_owner, publication.derived_owner):
        raise ProcessingError("publication_scope_mismatch")
    if not publication.raw_ready:
        raise ProcessingError("raw_publication_incomplete")
    if encoder is None:
        import tiktoken

        encoder = tiktoken.get_encoding(encoding)
    begin = time.monotonic()

    def channel(name):
        records = {r.memory_id: r for r in getattr(publication, name)}
        result = memoria.search(getattr(publication, name + "_owner"), query, top_k)
        items = []
        seen = set()
        for memory in result:
            record = records.get(memory.memory_id)
            if record is None or digest(memory.content) != record.content_sha256:
                raise ProcessingError("search_snapshot_mismatch")
            if memory.memory_id in seen:
                continue
            seen.add(memory.memory_id)
            items.append(
                {
                    "id": memory.memory_id,
                    "content": memory.content,
                    "channel": name,
                    "source_ids": record.source_ids,
                }
            )
        return items

    raw = channel("raw")
    derived = []
    state = "disabled"
    if enable_derived:
        state = "not_ready"
        if publication.derived_ready:
            try:
                derived = channel("derived") if publication.derived else []
                state = "ready"
            except (AdapterError, ProcessingError):
                state = "failed_raw_fallback"
    # Rank interleave instead of comparing incomparable upstream relevance scores.
    # At most 30% of top_k are derived; always prefer the leading raw record.
    limit = min(len(derived), math.floor(top_k * 0.3))
    merged, raw_index, derived_index = [], 0, 0
    while len(merged) < top_k and (raw_index < len(raw) or derived_index < limit):
        if derived_index < limit and (len(merged) % 3 == 2 or raw_index == len(raw)):
            merged.append(derived[derived_index])
            derived_index += 1
        else:
            merged.append(raw[raw_index])
            raw_index += 1
    data, seen_content = [], set()
    for item in merged:
        key = digest(item["content"])
        if key in seen_content:
            continue
        proposed = [*data, item]
        # Same whole-record evidence budget in both modes; answering instructions
        # and question require a separate reserve in the downstream evaluator.
        count = (
            len(encoder.encode(json.dumps(proposed, ensure_ascii=False), disallowed_special=()))
            + 256
        )
        if count > token_budget:
            break
        data = proposed
        seen_content.add(key)
    return {
        "data": data,
        "derived_status": state,
        "snapshot_hash": publication.snapshot_hash,
        "top_k": top_k,
        "evidence_token_budget": token_budget,
        "encoding": encoding,
        "merge_version": "rank-interleave-30pct-v1",
        "elapsed_seconds": time.monotonic() - begin,
        "raw_candidates": len(raw),
        "derived_candidates": len(derived),
        "selected_records": len(data),
    }
