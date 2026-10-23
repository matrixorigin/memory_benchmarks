"""Bounded offline extraction, resumable by immutable input and prompt contract."""

import json
import math
import os
import time
from dataclasses import dataclass, field
from itertools import groupby

import httpx
from pydantic import ValidationError

from ..evaluation import endpoint
from .models import Attempt, Extraction, History, Snapshot, WindowResult, digest, read_json
from .prompt import PROMPT_SHA256, PROMPT_VERSION, messages
from .validation import validate_candidate


class ProcessingError(ValueError):
    """Only fixed reason codes cross the CLI/logging boundary."""

    def __init__(self, reason, http_status=None):
        super().__init__(reason)
        self.http_status = http_status


@dataclass(frozen=True)
class ProcessorConfig:
    model: str = field(repr=False)
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    encoding: str = "o200k_base"
    input_tokens: int = 8192
    output_tokens: int = 4096
    window_messages: int = 12
    overlap_messages: int = 2
    timeout_seconds: float = 180

    def __post_init__(self):
        endpoint(self.base_url)
        if not self.model.strip() or not self.api_key.strip():
            raise ValueError("processor_configuration_invalid")
        if not (
            2048 <= self.input_tokens <= 128000
            and 256 <= self.output_tokens <= 16384
            and 1 <= self.window_messages <= 50
            and 0 <= self.overlap_messages < self.window_messages
            and math.isfinite(self.timeout_seconds)
            and 0 < self.timeout_seconds <= 600
        ):
            raise ValueError("processor_configuration_invalid")

    @classmethod
    def from_env(cls):
        return cls(
            model=os.environ["MEMORY_PROCESSOR_MODEL"],
            base_url=endpoint(os.environ["MEMORY_PROCESSOR_BASE_URL"]),
            api_key=os.environ["MEMORY_PROCESSOR_API_KEY"],
            encoding=os.getenv("MEMORY_PROCESSOR_ENCODING", "o200k_base"),
            input_tokens=int(os.getenv("MEMORY_PROCESSOR_INPUT_TOKENS", "8192")),
            output_tokens=int(os.getenv("MEMORY_PROCESSOR_OUTPUT_TOKENS", "4096")),
            window_messages=int(os.getenv("MEMORY_PROCESSOR_WINDOW_MESSAGES", "12")),
            overlap_messages=int(os.getenv("MEMORY_PROCESSOR_OVERLAP_MESSAGES", "2")),
            timeout_seconds=float(os.getenv("MEMORY_PROCESSOR_TIMEOUT_SECONDS", "180")),
        )

    def contract(self):
        return {
            "processor_version": "offline-extraction-v1",
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": PROMPT_SHA256,
            "model_fingerprint": digest([self.base_url.rstrip("/"), self.model]),
            "encoding": self.encoding,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "window_messages": self.window_messages,
            "overlap_messages": self.overlap_messages,
            "timeout_seconds": self.timeout_seconds,
            "temperature": 0,
            "token_accounting": "serialized-messages-plus-256-v1",
        }


class Extractor:
    def __init__(self, config, client, encoder=None):
        self.config, self.client = config, client
        if encoder is None:
            import tiktoken

            encoder = tiktoken.get_encoding(config.encoding)
        self.encoder = encoder

    def count(self, sources):
        return (
            len(
                self.encoder.encode(
                    json.dumps(messages(sources), ensure_ascii=False), disallowed_special=()
                )
            )
            + 256
        )

    def windows(self, history):
        # Contiguous groups preserve caller order; no cross-conversation context.
        for _, group in groupby(history.messages, key=lambda m: m.conversation_id):
            sources = list(group)
            start = 0
            while start < len(sources):
                end = start
                while end < min(len(sources), start + self.config.window_messages):
                    if self.count(sources[start : end + 1]) > self.config.input_tokens:
                        break
                    end += 1
                if end == start:
                    # Oversized individual messages are explicitly failed, never truncated.
                    end += 1
                yield sources[start:end]
                if end == len(sources):
                    break
                start = max(start + 1, end - self.config.overlap_messages)

    def call(self, sources):
        if self.count(sources) > self.config.input_tokens:
            raise ProcessingError("input_budget_exceeded")
        try:
            response = self.client.post(
                self.config.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + self.config.api_key},
                json={
                    "model": self.config.model,
                    "messages": messages(sources),
                    "temperature": 0,
                    "stream": False,
                    "max_tokens": self.config.output_tokens,
                },
                timeout=self.config.timeout_seconds,
            )
        except httpx.RequestError:
            raise ProcessingError("model_transport_error") from None
        if response.status_code != 200:
            raise ProcessingError("model_http_error", response.status_code)
        try:
            data = read_json(response.text)
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ProcessingError("model_completion_incomplete")
            output = Extraction.model_validate(read_json(choice["message"]["content"]))
            usage = data.get("usage") or {}
            usage = {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens")
                if isinstance(usage, dict) and type(usage.get(key)) is int and usage[key] >= 0
            }
            if usage.get("prompt_tokens", 0) > self.config.input_tokens:
                raise ProcessingError("provider_input_budget_exceeded")
        except ProcessingError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise ProcessingError("model_output_invalid") from None
        ids = {s.source_id for s in sources}
        if not set(output.unprocessed_source_ids) <= ids:
            raise ProcessingError("unprocessed_source_invalid")
        valid, rejected = [], 0
        for candidate in output.memories:
            try:
                if any(e.source_id in output.unprocessed_source_ids for e in candidate.evidence):
                    raise ValueError("unprocessed_citation")
                valid.append(validate_candidate(candidate, sources))
            except ValueError:
                rejected += 1
        return valid, output.unprocessed_source_ids, rejected, usage

    def run(self, history: History, *, enabled=False, previous=None, checkpoint=None):
        if not enabled:
            return None
        contract = self.config.contract()
        if previous is not None:
            if previous.history != history or previous.contract != contract:
                raise ProcessingError("resume_contract_mismatch")
            validate_snapshot(previous)
        cached = {r.window_id: r for r in previous.results} if previous else {}
        result = Snapshot(history=history, contract=contract)
        # Keep future cached windows in every checkpoint. Otherwise an interrupt
        # while retrying an early failed window loses later completed work.
        retained = dict(cached)
        for window in self.windows(history):
            source_ids = [s.source_id for s in window]
            window_id = digest([s.model_dump() for s in window])
            prior = cached.get(window_id)
            if prior and prior.status == "complete":
                record = prior
            else:
                begin = time.monotonic()
                try:
                    memories, pending, rejected, usage = self.call(window)
                    record = WindowResult(
                        window_id=window_id,
                        source_ids=source_ids,
                        status="partial" if pending or rejected else "complete",
                        reason="candidate_validation_failed"
                        if rejected
                        else ("unprocessed_sources" if pending else None),
                        memories=memories,
                        unprocessed_source_ids=pending,
                        usage=usage,
                    )
                except ProcessingError as exc:
                    record = WindowResult(
                        window_id=window_id,
                        source_ids=source_ids,
                        status="failed",
                        reason=str(exc),
                        http_status=exc.http_status,
                        unprocessed_source_ids=source_ids,
                    )
                record.elapsed_seconds = time.monotonic() - begin
                record.attempts = [
                    *(prior.attempts if prior else []),
                    Attempt(
                        status=record.status,
                        reason=record.reason,
                        http_status=record.http_status,
                        usage=record.usage,
                        elapsed_seconds=record.elapsed_seconds,
                    ),
                ]
            retained[window_id] = record
            result.results = list(retained.values())
            if checkpoint:
                checkpoint(result)
        return result


def validate_snapshot(snapshot):
    """Recheck loaded artifacts before publication/resume; no trust in model IDs."""
    by_id = {s.source_id: s for s in snapshot.history.messages}
    seen = set()
    for result in snapshot.results:
        if (
            result.window_id in seen
            or not result.source_ids
            or not set(result.source_ids) <= by_id.keys()
        ):
            raise ProcessingError("snapshot_window_invalid")
        seen.add(result.window_id)
        sources = [by_id[sid] for sid in result.source_ids]
        if len({s.conversation_id for s in sources}) != 1:
            raise ProcessingError("snapshot_scope_invalid")
        if result.window_id != digest([s.model_dump() for s in sources]):
            raise ProcessingError("snapshot_source_mismatch")
        if not set(result.unprocessed_source_ids) <= set(result.source_ids):
            raise ProcessingError("snapshot_unprocessed_invalid")
        if result.status == "failed" and result.memories:
            raise ProcessingError("snapshot_failed_has_memories")
        if result.status == "complete" and (result.unprocessed_source_ids or result.reason):
            raise ProcessingError("snapshot_completion_invalid")
        for memory in result.memories:
            try:
                validated = validate_candidate(memory, sources)
                if validated != memory:
                    raise ValueError("snapshot_time_not_validated")
            except (ValueError, ValidationError):
                raise ProcessingError("snapshot_candidate_invalid") from None
    return snapshot
