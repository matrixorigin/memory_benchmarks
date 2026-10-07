import hashlib
import json

from .config import Settings
from .errors import AdapterError
from .ingestion import message_parts, split_utf8  # noqa: F401 (compatibility export)
from .journal import Journal
from .memoria import Memoria
from .models import AddRequest, AddResponse, Evidence, SearchRequest, SearchResponse, text_content


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
        owner = self.owner(request.user_id)
        key = digest([self.settings.memoria_url.rstrip("/"), owner, request.request_id])
        fingerprint = digest(request.model_dump())
        with self.journal.lock(key):
            if not self.journal.begin(key, fingerprint):
                for position, body in enumerate(self.write_bodies(request, key)):
                    self.write(owner, key, position, body)
                self.journal.complete(key)
        return AddResponse(
            request_id=request.request_id, user_id=request.user_id, session_id=request.session_id
        )

    def write_bodies(self, request: AddRequest, key: str):
        session = digest([self.settings.namespace, request.user_id, request.session_id])
        for message_index, message in enumerate(request.messages):
            for part_index, (content, observed_at) in enumerate(message_parts(message)):
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

    def write(self, owner: str, key: str, position: int, body: dict) -> None:
        state, memory_id = self.journal.state(key, position)
        if state == "stored":
            return
        marker = body["extra_metadata"]["aml_write"]
        if state == "inflight":
            memory = (
                self.memoria.get(owner, memory_id)
                if memory_id
                else self.memoria.recover(owner, marker)
            )
            if memory is None:
                # A timeout/crash doesn't prove that Memoria stopped processing.
                # Reposting here could create duplicates, even after a negative query.
                raise AdapterError(503, "Prior write outcome is unknown; retry the same Add later")
        else:
            self.journal.record(key, position, "inflight")
            try:
                memory = self.memoria.store(owner, body)
            except AdapterError as exc:
                if exc.write_not_sent:
                    self.journal.record(key, position, "pending")
                raise
            self.journal.record(key, position, "inflight", memory.memory_id)

        # A point read alone does not prove visibility through the retrieval path.
        persisted = self.memoria.get(owner, memory.memory_id)
        if persisted.extra_metadata.get("aml_write") != marker:
            raise AdapterError(502, "Memoria did not persist the write marker")
        self.memoria.verify_searchable(owner, persisted, marker)
        self.journal.record(key, position, "stored", persisted.memory_id)

    def search(self, request: SearchRequest) -> SearchResponse:
        memories = self.memoria.search(
            self.owner(request.user_id), text_content(request.query), request.top_k
        )
        seen = set()
        evidence = []
        for memory in memories:
            if memory.memory_id in seen:
                continue
            seen.add(memory.memory_id)
            evidence.append(
                Evidence(
                    id=memory.memory_id,
                    content=memory.content,
                    score=memory.retrieval_score,
                    created_at=memory.created_at,
                )
            )
            if len(evidence) == request.top_k:
                break
        return SearchResponse(data=evidence)
