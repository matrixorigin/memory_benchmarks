import hashlib
import json
from itertools import chain, pairwise

from .budget import catalog_retry_budget, request_budget
from .budget import remaining as budget_remaining
from .config import Settings
from .embedding_tokens import TOKENIZER_SHA256
from .errors import AdapterError
from .ingestion import message_parts
from .journal import LEGACY_PROTOCOL, Journal
from .memoria import Memoria
from .models import AddRequest, AddResponse, Evidence, SearchRequest, SearchResponse, text_content
from .temporal import MAX_RESPONSE_CHARS, annotate


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class MemoryAdapter:
    def __init__(self, settings: Settings, memoria: Memoria, journal: Journal):
        self.settings = settings
        self.memoria = memoria
        self.journal = journal

    def owner(self, user_id: str) -> str:
        # Memoria owner IDs are <=64 bytes. Hash without trimming, truncation or
        # lossy character replacement, so long/Unicode benchmark IDs stay isolated.
        return digest([self.settings.namespace, user_id])

    def add(self, request: AddRequest) -> AddResponse:
        with (
            request_budget(self.settings.request_timeout_seconds),
            catalog_retry_budget(self.settings.catalog_retry_timeout_seconds),
            self.journal.metrics.add(),
        ):
            owner = self.owner(request.user_id)
            key = digest([self.settings.memoria_url.rstrip("/"), owner, request.request_id])
            fingerprint = digest(request.model_dump())
            with self.journal.lock(key):
                policy = self.journal.request_policy(
                    key,
                    fingerprint,
                    {
                        "version": 2,
                        "tokenizer_sha256": TOKENIZER_SHA256,
                        "max_tokens": self.settings.embedding_token_budget,
                    },
                )
                # One-item lookahead identifies the last chunk without buffering
                # the entire request, so its stored marker and completion commit together.
                bodies = chain(
                    self.write_bodies(request, key, policy) if policy is not None else [], [None]
                )
                for position, (body, following) in enumerate(pairwise(bodies)):
                    prepared = self.journal.prepare_write(
                        key, fingerprint, position, render_policy=policy
                    )
                    if prepared is None:
                        break
                    self.write(owner, key, position, body, prepared, last=following is None)
            budget_remaining(float("inf"))
            return AddResponse(
                request_id=request.request_id,
                user_id=request.user_id,
                session_id=request.session_id,
            )

    def write_bodies(self, request: AddRequest, key: str, policy: dict | None = None):
        # Explicit default keeps historical audit helpers and recovery fixtures v1.
        policy = policy if policy is not None else {"version": 1}
        if policy == {"version": 1} and type(policy["version"]) is int:
            token_budget = None
        elif (
            set(policy) == {"version", "tokenizer_sha256", "max_tokens"}
            and type(policy["version"]) is int
            and policy["version"] == 2
            and policy["tokenizer_sha256"] == TOKENIZER_SHA256
            and type(policy["max_tokens"]) is int
            and 128 <= policy["max_tokens"] <= 8192
        ):
            token_budget = policy["max_tokens"]
        else:
            raise AdapterError(503, "Unsupported journal rendering policy; check adapter version")
        session = digest([self.settings.namespace, request.user_id, request.session_id])
        for message_index, message in enumerate(request.messages):
            for part_index, (content, observed_at) in enumerate(
                message_parts(message, token_budget=token_budget)
            ):
                marker = digest([key, message_index, part_index])
                body = {
                    "content": content,
                    "memory_type": "episodic",
                    "session_id": session,
                    # Near-duplicate detection is subject-scoped. Separate evidence
                    # chunks must not supersede each other merely due to similarity.
                    "subject_id": marker,
                    "extra_metadata": {
                        "aml_write": marker,
                        "request_id": request.request_id,
                        "session_id": request.session_id,
                        "message_index": message_index,
                        "part_index": part_index,
                        "role": message.role,
                        "timestamp": message.timestamp,
                    },
                }
                if observed_at is not None:
                    body["observed_at"] = observed_at
                yield body

    def write(
        self,
        owner: str,
        key: str,
        position: int,
        body: dict,
        prepared: tuple[str, str | None, str],
        *,
        last: bool = False,
    ) -> None:
        state, memory_id, protocol = prepared
        if state == "stored":
            # Older journals may have persisted the final chunk but not complete.
            if last:
                self.journal.complete(key)
            return
        marker = body["extra_metadata"]["aml_write"]
        if state == "inflight" and memory_id:
            # Verification below handles temporary unreadability without a POST.
            pass
        elif state == "inflight" and protocol == LEGACY_PROTOCOL:
            memory = self.memoria.recover(owner, marker)
            if memory is None:
                # A negative query cannot prove an unknown legacy write failed.
                raise AdapterError(503, "Prior write outcome is unknown; retry the same Add later")
            memory_id = memory.memory_id
        else:
            # prepare_write committed the atomic intent before this call. Reusing
            # an existing atomic inflight row needs no additional SQLite commit.
            try:
                memory = self.memoria.store_idempotent(owner, body, marker)
            except AdapterError as exc:
                if exc.write_not_sent:
                    self.journal.record(key, position, "pending", protocol=protocol)
                raise
            memory_id = memory.memory_id
            self.journal.record(key, position, "inflight", memory_id, protocol=protocol)

        persisted = self.memoria.wait_for_visibility(owner, memory_id, marker)
        self.journal.record(
            key,
            position,
            "stored",
            persisted.memory_id,
            protocol=protocol,
            complete_request=last,
        )

    def search(self, request: SearchRequest) -> SearchResponse:
        with (
            request_budget(self.settings.request_timeout_seconds),
            catalog_retry_budget(self.settings.catalog_retry_timeout_seconds),
        ):
            result = self._search(request)
            budget_remaining(float("inf"))
            return result

    def _search(self, request: SearchRequest) -> SearchResponse:
        memories = self.memoria.search(
            self.owner(request.user_id), text_content(request.query), request.top_k
        )
        seen = set()
        evidence = []
        annotation_budget = MAX_RESPONSE_CHARS
        for memory in memories:
            if memory.memory_id in seen:
                continue
            seen.add(memory.memory_id)
            content = memory.content
            if self.settings.time_normalization_enabled:
                content = annotate(content, memory.extra_metadata, budget=annotation_budget)
                annotation_budget -= len(content) - len(memory.content)
            evidence.append(
                Evidence(
                    id=memory.memory_id,
                    content=content,
                    score=memory.retrieval_score,
                    created_at=memory.created_at,
                )
            )
            if len(evidence) == request.top_k:
                break
        return SearchResponse(data=evidence)
