import hashlib
import json

from benchmarks.textual.beam.dataset import load, normalize


def test_beam_keeps_gold_out_of_history_and_queries():
    record = {
        "sample_id": "100K-1",
        "chat": [
            {
                "batch_number": 1,
                "time_anchor": "2024-01-01",
                "turns": [[{"id": 0, "role": "user", "content": "original history"}]],
            }
        ],
        "probing_questions": {
            "knowledge_update": [
                {
                    "question": "What changed?",
                    "source_chat_ids": {"original_info": [0], "updated_info": [0]},
                    "answer": "GOLD SECRET",
                    "rubric": ["GOLD SECRET"],
                }
            ]
        },
    }
    result = normalize([record])[0]
    assert result["qa"][0]["evidence"] == ["0"]
    assert result["qa"][0]["question"] == "What changed?"
    assert "GOLD SECRET" not in str(result)
    assert result["conversation"]["session_1"][0]["text"] == "original history"


def test_beam_10m_plan_wrappers_and_abstention():
    record = {
        "sample_id": "10M-1",
        "chat": [
            {"plan-1": [{"turns": [[{"id": 100, "role": "assistant", "content": "history"}]]}]}
        ],
        "probing_questions": {"abstention": [{"question": "Unknown?", "source_chat_ids": [100]}]},
    }
    result = normalize([record])[0]
    assert result["conversation"]["session_1"][0]["dia_id"] == "100"
    assert result["qa"][0]["evidence"] == []


def test_manifest_load_is_lazy_and_verifies_hashes(tmp_path):
    directory = tmp_path / "chats/100K/1"
    (directory / "probing_questions").mkdir(parents=True)
    (directory / "chat.json").write_text("[]")
    (directory / "probing_questions/probing_questions.json").write_text("{}")
    files = {
        str(path.relative_to(tmp_path)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*.json")
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"variants": {"100K": 1}, "files": files}))
    samples = normalize(load(manifest))
    assert samples[0]["sample_id"] == "100K-1"
    assert samples[0]["conversation"] == {}
    assert samples[0]["qa"] == []
    assert "conversation" not in samples[0]  # No retained decoded cache.
    (directory / "chat.json").write_text("[{}]")
    import pytest

    with pytest.raises(ValueError, match="checksum mismatch"):
        load(manifest)
