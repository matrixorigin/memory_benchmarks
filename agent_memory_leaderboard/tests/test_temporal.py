import json

import pytest

from src.config import Settings
from src.temporal import MAX_RECORD_CHARS, annotate


def hints(text):
    return json.loads(text.split("calendar-hints-v1]\n", 1)[1])["hints"]


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("yesterday", "2023-07-14"),
        ("today", "2023-07-15"),
        ("tomorrow", "2023-07-16"),
        ("2 days ago", "2023-07-13"),
        ("昨天", "2023-07-14"),
        ("3天前", "2023-07-12"),
        ("last Friday", "2023-07-14"),
        ("next Saturday", "2023-07-22"),
        ("this Monday", "2023-07-10"),
        ("上周五", "2023-07-07"),
        ("本周天", "2023-07-16"),
        ("下周一", "2023-07-17"),
        ("last week", "2023-07-03..2023-07-09"),
        ("上周", "2023-07-03..2023-07-09"),
        ("this week", "2023-07-10..2023-07-16"),
        ("next week", "2023-07-17..2023-07-23"),
        ("last month", "2023-06"),
        ("下个月", "2023-08"),
        ("last year", "2022"),
        ("明年", "2024"),
    ],
)
def test_calendar_conventions(expression, expected):
    content = f"[user]\n[1:51 pm on 15 July, 2023] Person: {expression} I visited a museum."
    result = annotate(content)
    assert result.startswith(content)
    assert hints(result)[0]["calendar_value"] == expected
    assert hints(result)[0]["expression"] == expression


@pytest.mark.parametrize(
    "stamp,expression,expected",
    [
        ("2024-03-01T01:00:00Z", "yesterday", "2024-02-29"),
        ("2023-01-01T01:00:00Z", "last month", "2022-12"),
        ("2023-12-31T01:00:00Z", "next month", "2024-01"),
        ("2023-07-14T01:00:00Z", "last Friday", "2023-07-07"),
        ("2023-01-01T01:00:00Z", "last week", "2022-12-19..2022-12-25"),
    ],
)
def test_calendar_edges(stamp, expression, expected):
    text = annotate(f"[user at {stamp}]\n{expression} I visited a museum.")
    assert hints(text)[0]["calendar_value"] == expected


@pytest.mark.parametrize(
    "content,metadata",
    [
        ("[user]\nyesterday I visited a museum.", {}),
        ("[user]\n[1:00 pm on 31 February, 2023] yesterday", {}),
        ("[user at invalid]\nyesterday", {}),
        ("[user at 2023-07-15T00:00:00Z]\nNo relative time here.", {}),
        ("[user at 2023-07-15T00:00:00Z]\nShe said: 'yesterday'.", {}),
        ('[user at 2023-07-15T00:00:00Z]\nShe said: "yesterday".', {}),
        ("[user at 2023-07-15T00:00:00Z]\nShe said: “yesterday”.", {}),
        ('[user at 2023-07-15T00:00:00Z]\n"yesterday', {}),
        ("[user at 2023-07-15T00:00:00Z]\nyesterday", {"part_index": 1}),
        ("[user at 2023-07-15T00:00:00Z]\nyesterday", {"timestamp": 0}),
        ("[user at 2023-07-15T00:00:00Z]\n[1:00 pm on 16 July, 2023] yesterday", {}),
        ("[user]\nyesterday", {"timestamp": True}),
        ("[user]\nyesterday", {"timestamp": 10**100}),
        ("[user at 0001-01-01T00:00:00Z]\nyesterday last year last month", {}),
        ("[user at 2023-07-15T00:00:00Z]\nlast weekend", {}),
        ("[user at 2023-07-15T00:00:00Z]\n上周末 1000天前", {}),
        ("[user]\n[1:00 pm on 15 July, 2023] yesterday [1:00 pm on 16 July, 2023]", {}),
    ],
)
def test_unsupported_or_unanchored_content_unchanged(content, metadata):
    assert annotate(content, metadata) == content


def test_metadata_anchor_is_message_time_not_created_at():
    raw = "[user]\nyesterday I visited a museum."
    assert hints(annotate(raw, {"timestamp": 0}))[0]["calendar_value"] == "1969-12-31"
    assert annotate(raw, {"created_at": "2023-07-15T00:00:00Z"}) == raw


def test_quotations_and_contractions():
    raw = '[user at 2023-07-15T00:00:00Z]\nI didn\'t go yesterday. She said "tomorrow".'
    assert [v["expression"] for v in hints(annotate(raw))] == ["yesterday"]


def test_annotations_are_optional_and_bounded_not_original_slots():
    raw = (
        "[user at 2023-07-15T00:00:00Z]\nYesterday today tomorrow last week "
        "next week last month next year"
    )
    assert annotate(raw, budget=0) == raw
    assert annotate(raw, budget=20) == raw
    result = annotate(raw, budget=450)
    assert result.startswith(raw)
    assert len(result) - len(raw) <= 450
    assert len(annotate(raw)) - len(raw) <= MAX_RECORD_CHARS


def test_env_flag_explicit_and_default_off(monkeypatch):
    monkeypatch.setenv("MEMORIA_URL", "http://example.test")
    monkeypatch.setenv("MEMORIA_MASTER_KEY", "synthetic")
    monkeypatch.delenv("MEMORY_TIME_NORMALIZATION_ENABLED", raising=False)
    assert Settings.from_env().time_normalization_enabled is False
    monkeypatch.setenv("MEMORY_TIME_NORMALIZATION_ENABLED", "true")
    assert Settings.from_env().time_normalization_enabled is True
    monkeypatch.setenv("MEMORY_TIME_NORMALIZATION_ENABLED", "maybe")
    with pytest.raises(ValueError, match="MEMORY_TIME_NORMALIZATION_ENABLED"):
        Settings.from_env()
