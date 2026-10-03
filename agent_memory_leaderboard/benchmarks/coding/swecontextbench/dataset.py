"""SWEContextBench historical-issue retrieval diagnostic (not patch correctness)."""

DEFAULT_PATH = "benchmarks/coding/swecontextbench/data/retrieval.json"
SOURCE = "https://huggingface.co/datasets/jiayuanz3/SWEContextBench"
EVIDENCE_UNIT = "related historical issue"


def normalize(dataset):
    return dataset
