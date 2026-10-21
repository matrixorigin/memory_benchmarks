"""Unmodified JSON extraction helpers from LoCoMo-Refined.

Source: https://github.com/mem-eval-suite/LoCoMo_refined/blob/
887091190789e8d6760e70b9edd696539923dc4f/src/llm_judge_runtime.py
CC BY-NC 4.0; see LOCOMO_LICENSE.txt and LOCOMO_NOTICE.txt.
"""

def extract_json_object(text: str) -> str:
    stripped = _strip_code_fence(text).strip()
    in_string = False
    escape = False
    depth = 0
    start: int | None = None
    for index, ch in enumerate(stripped):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = index
            depth += 1
        elif ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start is not None:
                return stripped[start : index + 1]
    raise ValueError("failed to extract JSON object from judge response")

def _strip_code_fence(content: str) -> str:
    stripped = (content or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped
