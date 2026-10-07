import pytest

from src.ingestion import MAX_MEMORY_BYTES, message_parts, split_utf8
from src.models import Message


@pytest.mark.parametrize("limit", [4, 5, 7, 32768])
def test_split_utf8_preserves_all_codepoints_and_byte_bounds(limit):
    text = "a中🙂é" * 20
    parts = list(split_utf8(text, limit))
    assert "".join(parts) == text
    assert all(0 < len(part.encode()) <= limit for part in parts)


def test_split_empty_and_invalid_limit():
    assert list(split_utf8("", 4)) == []
    with pytest.raises(ValueError):
        list(split_utf8("a", 3))


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (None, None),
        (0, "1970-01-01T00:00:00.000Z"),
        (-1, "1969-12-31T23:59:59.999Z"),
        (1704067200123, "2024-01-01T00:00:00.123Z"),
    ],
)
def test_message_timestamp_uses_source_milliseconds(timestamp, expected):
    message = Message(role="assistant", content="fact", timestamp=timestamp)
    header = f"[assistant at {expected}]\n" if expected else "[assistant]\n"
    assert list(message_parts(message)) == [(header + "fact", expected)]


def test_message_chunks_include_header_in_byte_limit_and_join_text_parts():
    message = Message.model_validate(
        {
            "role": "user",
            "timestamp": 0,
            "content": [
                {"type": "text", "text": "中" * 22000},
                {"type": "text", "text": "tail"},
            ],
        }
    )
    parts = list(message_parts(message))
    header = "[user at 1970-01-01T00:00:00.000Z]\n"
    assert len(parts) == 3
    assert all(
        text.startswith(header) and len(text.encode()) <= MAX_MEMORY_BYTES for text, _ in parts
    )
    assert "".join(text[len(header) :] for text, _ in parts) == "中" * 22000 + "\ntail"
