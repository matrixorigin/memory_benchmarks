import json

import pytest

from benchmarks.textual.clbench.dataset import normalize as clbench
from benchmarks.textual.personamem_v2.dataset import normalize as persona


def test_clbench_keeps_final_task_and_rubrics_out_of_history():
    sample = {
        "metadata": {"task_id": "t", "context_category": "rules"},
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "prior reference"},
            {"role": "assistant", "content": "prior response"},
            {"role": "user", "content": "held-out task"},
        ],
        "rubrics": ["GOLD RUBRIC"],
    }
    parsed = clbench([sample])[0]
    history = json.dumps(parsed["conversation"])
    assert "prior reference" in history and "prior response" in history
    assert "held-out task" not in history and "GOLD RUBRIC" not in json.dumps(parsed)
    assert parsed["qa"][0]["evidence"] == []


def test_clbench_single_turn_is_search_only():
    parsed = clbench(
        [
            {
                "metadata": {"task_id": "t", "context_category": "rules"},
                "messages": [{"role": "user", "content": "context and task"}],
            }
        ]
    )[0]
    assert not parsed["conversation"]["session_1"]
    assert parsed["qa"][0]["question"] == "context and task"


def test_clbench_rejects_answer_as_final_message():
    with pytest.raises(ValueError):
        clbench([{"messages": [{"role": "assistant", "content": "gold"}]}])


def test_persona_excludes_profile_and_labels():
    sample = {
        "persona_id": 2,
        "history": [
            {"role": "system", "content": "GOLD PROFILE"},
            {"role": "user", "content": "implicit preference in dialogue"},
            {"role": "assistant", "content": "historical reply"},
        ],
        "questions": [
            {
                "user_query": "{'role': 'user', 'content': 'test query'}",
                "topic_query": "health",
                "correct_answer": "GOLD ANSWER",
                "preference": "GOLD PREFERENCE",
                "related_conversation_snippet": "GOLD SNIPPET",
            }
        ],
    }
    parsed = persona([sample])[0]
    assert "GOLD" not in json.dumps(parsed)
    assert "test query" not in json.dumps(parsed["conversation"])
    assert len(parsed["conversation"]["session_1"]) == 2
    assert parsed["qa"][0]["evidence"] == []


def test_clbench_jsonl_preserves_unicode_line_separator(tmp_path):
    from benchmarks.textual.clbench.dataset import load

    path = tmp_path / "data.jsonl"
    path.write_text(json.dumps({"content": "a\u2028b"}, ensure_ascii=False) + "\n")
    assert load(path) == [{"content": "a\u2028b"}]
