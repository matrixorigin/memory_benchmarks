"""CL-bench conversation-history retrieval; final user message stays query-side."""

import json
from pathlib import Path

DEFAULT_PATH = "benchmarks/textual/clbench/data/CL-bench.jsonl"
SOURCE = "https://huggingface.co/datasets/tencent/CL-bench"
EVIDENCE_UNIT = None


def load(path):
    return [json.loads(line) for line in Path(path).read_text().split("\n") if line.strip()]


def normalize(dataset):
    result = []
    for item in dataset:
        messages = item["messages"]
        if not messages or messages[-1]["role"] != "user":
            raise ValueError("CL-bench must end with the held-out user task")
        ident = item["metadata"]["task_id"]
        turns = []
        for index, message in enumerate(messages[:-1]):
            if message["role"] not in {"user", "assistant"}:
                continue
            turns.append(
                {
                    "dia_id": f"{ident}:m{index}",
                    "role": message["role"],
                    "speaker": message["role"],
                    "text": message["content"],
                }
            )
        result.append(
            {
                "sample_id": ident,
                "conversation": {"session_1_date_time": "unspecified", "session_1": turns},
                "qa": [
                    {
                        "id": ident,
                        "question": messages[-1]["content"],
                        "category": item["metadata"]["context_category"],
                        "evidence": [],
                    }
                ],
            }
        )
    return result
