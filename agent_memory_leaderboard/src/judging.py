"""Build judge requests from pinned upstream prompts, without rewriting their rules."""

import hashlib
import json
import re
from pathlib import Path

from .judges.locomo import extract_json_object
from .judges.longmemeval import get_anscheck_prompt

_ROOT = Path(__file__).parent / "judges"
LOCOMO_TEMPLATE = (_ROOT / "locomo_refined.txt").read_text(encoding="utf-8")
LOCOMO_REVISION = "887091190789e8d6760e70b9edd696539923dc4f"
LONGMEMEVAL_REVISION = "9e0b455f4ef0e2ab8f2e582289761153549043fc"
PROFILES = {
    "locomo": {
        "source": f"https://github.com/mem-eval-suite/LoCoMo_refined/blob/{LOCOMO_REVISION}/src/llm_judge.py",
        "revision": LOCOMO_REVISION,
        "prompt_sha256": hashlib.sha256(LOCOMO_TEMPLATE.encode()).hexdigest(),
        "response_format": "json_label_correct_wrong",
        "aggregation": "one_judgment_per_gold_candidate_max",
        "max_output_tokens": None,
        "request_options": {"enable_thinking": False},
    },
    "longmemeval": {
        "source": f"https://github.com/xiaowu0162/LongMemEval/blob/{LONGMEMEVAL_REVISION}/src/evaluation/evaluate_qa.py",
        "revision": LONGMEMEVAL_REVISION,
        "prompt_file_sha256": hashlib.sha256((_ROOT / "longmemeval.py").read_bytes()).hexdigest(),
        "response_format": "yes_no",
        "aggregation": "one_reference",
        "max_output_tokens": 10,
        "request_options": {"n": 1},
    },
}


def model_matches_upstream(kind, model):
    if kind == "locomo":
        alias = re.sub(r"[^a-z0-9]+", "", model.lower())
        return alias.endswith(("qwen314b", "qwenqwen314b"))
    return model in {
        "gpt-4o-2024-08-06",
        "gpt-4o-mini-2024-07-18",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
    }


def judge_requests(kind, question, references, prediction):
    if kind == "locomo":
        prompts = [
            LOCOMO_TEMPLATE.format(
                question=question["question"], gold_answer=reference, generated_answer=prediction
            )
            for reference in references
        ]
    else:
        if len(references) != 1:
            raise ValueError("LongMemEval requires one reference per question")
        category = question["category"]
        prompts = [
            get_anscheck_prompt(
                category,
                question["question"],
                references[0],
                prediction,
                abstention=category == "abstention",
            )
        ]
    return [[{"role": "user", "content": prompt}] for prompt in prompts]


def parse_label(value, kind):
    """Preserve the pinned upstream label decisions, including permissive handling."""
    if kind == "longmemeval":
        return int("yes" in value.strip().lower())
    label = json.loads(extract_json_object(value))
    return int(str(label.get("label", "")).strip().upper() == "CORRECT")
