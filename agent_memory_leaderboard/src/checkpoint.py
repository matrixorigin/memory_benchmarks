"""Atomic checkpoints with one atomic result file per completed question."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def question_dir(path):
    return path.parent / (path.stem + ".questions")


def save_question(path, qid, record):
    target = question_dir(path) / (hashlib.sha256(qid.encode()).hexdigest() + ".json")
    save(target, {"question_id": qid, "record": record})


def load(path):
    report = json.loads(path.read_text())
    if report.get("checkpoint_format") == 2:
        for entry in question_dir(path).glob("*.json"):
            item = json.loads(entry.read_text())
            expected = hashlib.sha256(item["question_id"].encode()).hexdigest() + ".json"
            if entry.name != expected:
                raise ValueError("Invalid question checkpoint identity")
            report["questions"][item["question_id"]] = item["record"]
    return report


def checkpoint(path, report, *, final=False):
    # Completed/failed final reports remain self-contained for existing consumers.
    # A killed process can be recovered by load(), merging atomic question files.
    save(path, report if final else report | {"questions": {}})
