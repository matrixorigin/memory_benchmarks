"""BEAM's published chat JSON; only original messages enter memory."""

import hashlib
import json
from pathlib import Path

DEFAULT_PATH = "benchmarks/textual/beam/data/manifest.json"
SOURCE = "https://github.com/mohammadtavakoli78/BEAM"
EVIDENCE_UNIT = "message"


def load(path):
    """Verify pinned files and return small descriptors, not all decoded histories."""
    path = Path(path)
    manifest = json.loads(path.read_text())
    for relative, expected in manifest["files"].items():
        with (path.parent / relative).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"BEAM checksum mismatch: {relative}")
    return [
        {"sample_id": f"{size}-{i}", "directory": path.parent / f"chats/{size}/{i}"}
        for size, count in manifest["variants"].items()
        for i in range(1, count + 1)
    ]


class _LazySample(dict):
    def __getitem__(self, key):
        if key == "sample_id":
            return super().__getitem__(key)
        directory = super().__getitem__("directory")
        # Do not cache decoded histories: the driver retains the descriptor list.
        item = {
            "sample_id": self["sample_id"],
            "chat": json.loads((directory / "chat.json").read_text())
            if key == "conversation"
            else [],
            "probing_questions": json.loads(
                (directory / "probing_questions/probing_questions.json").read_text()
            ),
        }
        return normalize([item])[0][key]


def _batches(chat):
    for item in chat:
        if "turns" in item:
            yield item
        else:
            # The official 10M release wraps batches in plan-N objects.
            for key, value in item.items():
                if key.startswith("plan-") and value is not None:
                    yield from _batches(value)


def _ids(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ids(child)
    elif isinstance(value, (str, int)):
        yield str(value)
    elif value is not None:
        raise ValueError("Unsupported BEAM source_chat_ids value")


def normalize(dataset):
    result = []
    for item in dataset:
        if "directory" in item:
            result.append(_LazySample(item))
            continue
        conversation = {}
        for index, batch in enumerate(_batches(item["chat"]), 1):
            name = f"session_{index}"
            conversation[name + "_date_time"] = batch.get("time_anchor") or "date unspecified"
            conversation[name] = [
                {
                    "dia_id": str(message["id"]),
                    "speaker": message["role"],
                    "role": message["role"],
                    "text": message["content"],
                }
                for turn in batch["turns"]
                for message in turn
            ]
        qa = []
        for category, questions in item["probing_questions"].items():
            for index, question in enumerate(questions):
                qa.append(
                    {
                        "id": f"{item['sample_id']}:{category}:{index}",
                        "question": question["question"],
                        "category": category,
                        "evidence": []
                        if category == "abstention"
                        else sorted(set(_ids(question.get("source_chat_ids", [])))),
                    }
                )
        result.append({"sample_id": item["sample_id"], "conversation": conversation, "qa": qa})
    return result
