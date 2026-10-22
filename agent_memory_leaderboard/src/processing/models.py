"""History-only inputs and bounded, strict model outputs."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import Nonblank

Text = Annotated[Nonblank, Field(max_length=4000)]
Identity = Annotated[Nonblank, Field(max_length=256)]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def read_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("invalid_json_number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(StrictModel):
    source_id: Identity
    source_version: Identity
    conversation_id: Identity
    speaker: Identity | None
    role: Literal["user", "assistant"]
    content: Annotated[Nonblank, Field(max_length=100000)]
    message_time: Annotated[str, Field(max_length=100)] | None
    timezone: Annotated[str, Field(max_length=100)] | None


class History(StrictModel):
    # Explicit data declaration: this CLI is not an official-payload capture tool.
    data_policy: Literal["public", "synthetic"]
    user_id: Identity
    messages: Annotated[list[Source], Field(min_length=1, max_length=100000)]

    @model_validator(mode="after")
    def unique_sources(self):
        if len({m.source_id for m in self.messages}) != len(self.messages):
            raise ValueError("duplicate_source_id")
        return self


class EventTime(StrictModel):
    original_expression: Text | None
    start: Annotated[str, Field(max_length=100)] | None
    end: Annotated[str, Field(max_length=100)] | None
    precision: Literal["datetime", "day", "month", "year", "interval", "unknown", "not_applicable"]
    timezone: Annotated[str, Field(max_length=100)] | None
    anchor_source_ids: Annotated[list[Identity], Field(max_length=20)]


class Citation(StrictModel):
    source_id: Identity
    quote: Text


class Candidate(StrictModel):
    text: Text
    kind: Literal["fact", "event"]
    subjects: Annotated[list[Identity], Field(min_length=1, max_length=20)]
    assertion: Literal["explicit", "reported", "planned", "hypothetical", "uncertain"]
    event_status: Literal[
        "planned", "ongoing", "completed", "cancelled", "unknown", "not_applicable"
    ]
    event_time: EventTime
    evidence: Annotated[list[Citation], Field(min_length=1, max_length=20)]
    uncertainties: Annotated[list[Text], Field(max_length=20)]


class Extraction(StrictModel):
    memories: Annotated[list[Candidate], Field(max_length=100)]
    unprocessed_source_ids: Annotated[list[Identity], Field(max_length=100)]


class Attempt(StrictModel):
    status: Literal["complete", "partial", "failed"]
    reason: str | None
    http_status: int | None
    usage: dict[str, int]
    elapsed_seconds: float


class WindowResult(StrictModel):
    window_id: str
    source_ids: list[str]
    status: Literal["complete", "partial", "failed"]
    reason: str | None = None
    http_status: int | None = None
    memories: list[Candidate] = Field(default_factory=list)
    unprocessed_source_ids: list[str] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    elapsed_seconds: float = 0.0
    attempts: list[Attempt] = Field(default_factory=list)


class Snapshot(StrictModel):
    format_version: Literal[1] = 1
    history: History
    contract: dict
    results: list[WindowResult] = Field(default_factory=list)

    @property
    def complete(self):
        covered = {sid for r in self.results for sid in r.source_ids}
        return (
            bool(self.results)
            and all(r.status == "complete" for r in self.results)
            and covered == {s.source_id for s in self.history.messages}
        )

    def memories(self):
        unique = {}
        for result in self.results:
            for memory in result.memories:
                # Canonicalize citation order to deduplicate overlap windows.
                item = memory.model_dump()
                item["evidence"] = sorted(
                    item["evidence"], key=lambda c: (c["source_id"], c["quote"])
                )
                unique.setdefault(digest(item), memory)
        return unique
