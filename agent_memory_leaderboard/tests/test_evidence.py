import json

from src.evidence import SourceManifest, prefix_recalls, recall, source_messages
from src.ingestion import message_parts
from src.models import Message


def conversation(texts):
    return {
        "session_1_date_time": "date",
        "session_1": [
            {"dia_id": f"D1:{index}", "speaker": "A", "text": text}
            for index, text in enumerate(texts, 1)
        ],
    }


def test_manifest_matches_original_and_adapter_rendered_content_without_labels():
    data = conversation(["private fact"])
    manifest = SourceManifest(data)
    body = next(source_messages(data))[-1]
    rendered = list(message_parts(Message.model_validate(body)))[0][0]
    assert manifest.match(body["content"]) == ["D1:1"]
    assert manifest.match(rendered) == ["D1:1"]
    assert "private fact" not in json.dumps(manifest.dump())
    assert "[dialogue:" not in rendered


def test_duplicate_content_is_ambiguous_and_never_credited():
    data = conversation(["same fact", "same fact"])
    manifest = SourceManifest(data)
    content = next(source_messages(data))[-1]["content"]
    ranked, audit = manifest.audit([content, "unknown"])
    assert ranked == [[], []]
    assert audit == {"mapped_records": 0, "ambiguous_records": 1, "unmapped_records": 1}
    assert recall(["D1:1", "D1:2"], ranked) == 0


def test_long_utf8_message_matches_each_adapter_chunk():
    data = conversation(["记忆" * 16000 + "ending"])
    manifest = SourceManifest(data)
    body = next(source_messages(data))[-1]
    parts = list(message_parts(Message.model_validate(body)))
    assert len(parts) >= 3
    assert all(manifest.match(text) == ["D1:1"] for text, _ in parts)


def test_redacted_content_stays_unmapped_instead_of_inventing_provenance():
    manifest = SourceManifest(conversation(["phone 12345"]))
    assert manifest.audit(["[user]\n[date] A: phone [phone]"])[1]["unmapped_records"] == 1


def test_recall_deduplicates_exact_ids_and_preserves_ranked_prefix():
    ranked = [["D1:1"], ["D1:1"], ["D1:2"]]
    assert recall(["D1:1", "D1:1", "D1:2"], ranked) == 1
    assert recall([], ranked) is None
    assert recall(["D1:10"], ranked) == 0
    assert prefix_recalls(["D1:1", "D1:2"], ranked, 3) == {"1": 0.5, "3": 1}
