"""Content-hash source attribution; diagnostic labels never enter Add/Search."""

import hashlib
import re

from .ingestion import RENDER_VERSION, message_parts
from .models import Message


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_messages(conversation):
    sessions = sorted(
        (s for s in conversation if re.fullmatch(r"session_\d+", s)),
        key=lambda s: int(s.split("_")[1]),
    )
    for session in sessions:
        for index, turn in enumerate(conversation[session]):
            yield (
                session,
                index,
                turn["dia_id"],
                {
                    "role": turn.get("role", "user"),
                    "content": (
                        f"[{conversation[session + '_date_time']}] "
                        f"{turn['speaker']}: {turn['text']}"
                    ),
                },
            )


class SourceManifest:
    def __init__(self, conversation):
        self.hashes = {}
        self.entries = []
        for session, index, source, body in source_messages(conversation):
            parts = [content for content, _ in message_parts(Message.model_validate(body))]
            # Include exact wire content for adapters returning the original message.
            hashes = sorted({content_hash(body["content"]), *(content_hash(p) for p in parts)})
            for value in hashes:
                self.hashes.setdefault(value, set()).add(source)
            self.entries.append(
                {
                    "session": session,
                    "message_index": index,
                    "source_id": source,
                    "content_hashes": hashes,
                }
            )

    def dump(self):
        return {
            "version": 1,
            "render_version": RENDER_VERSION,
            "method": "exact-content-sha256",
            "entries": self.entries,
        }

    def match(self, content):
        candidates = self.hashes.get(content_hash(content), set())
        # Identical text across different evidence units cannot establish provenance.
        return sorted(candidates) if len(candidates) == 1 else []

    def audit(self, contents):
        mapped = [self.match(content) for content in contents]
        return mapped, {
            "mapped_records": sum(bool(ids) for ids in mapped),
            "ambiguous_records": sum(
                len(self.hashes.get(content_hash(c), set())) > 1 for c in contents
            ),
            "unmapped_records": sum(content_hash(c) not in self.hashes for c in contents),
        }


def recall(expected, ranked_ids):
    expected = set(expected)
    found = {source for ids in ranked_ids for source in ids}
    return len(expected & found) / len(expected) if expected else None


def prefix_recalls(expected, ranked_ids, top_k):
    return {
        str(k): recall(expected, ranked_ids[:k])
        for k in sorted({1, 5, 10, 20, 50, 100, top_k})
        if k <= top_k
    }
