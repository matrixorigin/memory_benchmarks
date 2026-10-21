"""LongMemEval reader framing, applied only to actual retrieved content.

Templates and session formatting: LongMemEval, MIT, copyright (c) 2024 Di Wu.
https://github.com/xiaowu0162/LongMemEval/blob/
9e0b455f4ef0e2ab8f2e582289761153549043fc/src/generation/run_generation.py
See readers/LONGMEMEVAL_LICENSE.txt. Record grouping is this adapter's integration.
"""

import hashlib
import json
from pathlib import Path

CONTEXT_WINDOWS = {
    "gpt-4o": 128000,
    "gpt-4o-2024-08-06": 128000,
    "gpt-4o-mini-2024-07-18": 128000,
    "meta-llama/Meta-Llama-3.1-8B-Instruct": 128000,
    "meta-llama/Meta-Llama-3.1-70B-Instruct": 128000,
    "microsoft/Phi-3-medium-128k-instruct": 120000,
    "microsoft/Phi-3.5-mini-instruct": 120000,
    "microsoft/phi-4": 16000,
    "mistral-7b-instruct-v0.2": 32000,
    "mistral-7b-instruct-v0.3": 32000,
    "In2Training/FILM-7B": 32000,
}

_ROOT = Path(__file__).parent / "readers"
TEMPLATES = {
    mode: (_ROOT / f"longmemeval_{mode}.txt").read_text(encoding="utf-8")
    for mode in ("con", "direct")
}
READER_PROFILE = {
    "source": "https://github.com/xiaowu0162/LongMemEval/blob/"
    "9e0b455f4ef0e2ab8f2e582289761153549043fc/src/generation/run_generation.py",
    "version": "longmemeval-retrieved-only-reader-v1",
    "template_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in TEMPLATES.items()},
    "history_format": "json",
    "useronly": False,
    "selection_unit": "adapter_returned_record_not_full_session",
    "unknown_source_policy": "retain_verbatim_with_unknown_date_and_role",
    "truncation": "chronological_history_token_prefix_with_full_request_cap",
}


def context(question, contents, metadata, encoder, input_limit, output_tokens, mode, count):
    """Return the prompt and exact original ranks retained after token truncation."""
    date = question.get("question_date")
    if not isinstance(date, str) or not date.strip():
        raise ValueError("question_date_required")
    if metadata is None:
        metadata = [None] * len(contents)
    if len(metadata) != len(contents):
        raise ValueError("reader_metadata_length_mismatch")
    groups = {}
    unknown = 0
    for rank, (content, info) in enumerate(zip(contents, metadata, strict=True)):
        if info is None:
            unknown += 1
            info = {
                "session": f"unknown-{rank}",
                "date": "unknown",
                "role": "unknown",
                "content": content,
                "message_index": rank,
                "part_index": 0,
            }
        # Metadata is obtained only by exact hashes of the content that was returned.
        key = (info["date"], info["session"])
        groups.setdefault(key, []).append((rank, info))
    history, spans = "", []
    for index, ((session_date, _), turns) in enumerate(
        sorted(groups.items(), key=lambda item: (item[0][0] == "unknown", item[0][0])), 1
    ):
        turns.sort(key=lambda item: (item[1]["message_index"], item[1]["part_index"], item[0]))
        history += f"\n### Session {index}:\nSession Date: {session_date}\nSession Content:\n\n["
        for turn_index, (rank, info) in enumerate(turns):
            if turn_index:
                history += ", "
            begin = len(history)
            history += json.dumps({"role": info["role"], "content": info["content"]})
            spans.append((rank, begin, len(history)))
        history += "]\n"

    def messages(text):
        return [
            {
                "role": "user",
                "content": TEMPLATES[mode].format(
                    text,
                    date,
                    question["question"],
                ),
            }
        ]

    if count(messages("")) > input_limit:
        raise ValueError("answer_input_budget_exceeded")
    # Upstream reserves generation tokens plus 1000 tokens before truncating history.
    tokens = encoder.encode(history, allowed_special={"<|endoftext|>"})
    limit = max(0, input_limit - output_tokens - 1000)
    limit = min(len(tokens), limit)
    kept = history if limit == len(tokens) else encoder.decode(tokens[:limit])
    # Enforce the actual configured full-request cap as well as upstream's reserve.
    if count(messages(kept)) > input_limit:
        low, high = 0, limit
        while low < high:
            middle = (low + high + 1) // 2
            if count(messages(encoder.decode(tokens[:middle]))) <= input_limit:
                low = middle
            else:
                high = middle - 1
        kept = encoder.decode(tokens[:low])
    final = messages(kept)
    if count(final) > input_limit:
        raise ValueError("answer_input_budget_exceeded")
    included = sorted(rank for rank, _, end in spans if end <= len(kept))
    partial = sorted(rank for rank, begin, end in spans if begin < len(kept) < end)
    return final, {
        "context_records": len(included),
        "context_record_indices": included,
        "partial_context_record_indices": partial,
        "reader_unmapped_records": unknown,
        "input_tokens_estimate": count(final),
    }
