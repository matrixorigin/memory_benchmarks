"""Mechanical checks are not a semantic entailment judge."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Candidate, EventTime


def anchor_date(source):
    value = source.message_time
    if value is None:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        stamp = datetime.fromisoformat(value)
        if source.timezone:
            zone = ZoneInfo(source.timezone)
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(zone)
        return stamp.date()
    except (ValueError, KeyError, OverflowError):
        return None


def normalized_time(expression, anchors):
    """Only exact ISO dates and a small deterministic relative-date grammar."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expression):
        try:
            return date.fromisoformat(expression).isoformat(), "day"
        except ValueError:
            return None
    if len(anchors) != 1 or (base := anchor_date(anchors[0])) is None:
        return None
    expr = expression.lower().strip()
    offset = {"yesterday": -1, "today": 0, "tomorrow": 1, "昨天": -1, "今天": 0, "明天": 1}.get(
        expr
    )
    ago = re.fullmatch(r"(\d{1,4}) days? ago", expr) or re.fullmatch(r"(\d{1,4})天前", expr)
    if ago:
        offset = -int(ago[1])
    if offset is not None:
        try:
            return (base + timedelta(days=offset)).isoformat(), "day"
        except OverflowError:
            return None
    month_offset = {
        "next month": 1,
        "this month": 0,
        "last month": -1,
        "下个月": 1,
        "这个月": 0,
        "上个月": -1,
    }.get(expr)
    if month_offset is not None:
        year, month = divmod(base.year * 12 + base.month - 1 + month_offset, 12)
        if 1 <= year <= 9999:
            return f"{year:04d}-{month + 1:02d}", "month"
    return None


def validate_candidate(candidate: Candidate, sources):
    by_id = {s.source_id: s for s in sources}
    cited = set()
    for evidence in candidate.evidence:
        source = by_id.get(evidence.source_id)
        if source is None or evidence.quote not in source.content:
            raise ValueError("invalid_citation")
        cited.add(evidence.source_id)
    event = candidate.event_time
    if not set(event.anchor_source_ids) <= cited:
        raise ValueError("invalid_time_anchor")
    if len(set(event.anchor_source_ids)) != len(event.anchor_source_ids):
        raise ValueError("duplicate_time_anchor")
    if candidate.assertion == "planned" and candidate.event_status == "completed":
        raise ValueError("inconsistent_event_status")
    expression = event.original_expression
    if expression and not any(expression in e.quote for e in candidate.evidence):
        raise ValueError("unquoted_time_expression")
    if event.start is None and event.end is None:
        if event.precision not in {"unknown", "not_applicable"} or event.timezone is not None:
            raise ValueError("inconsistent_event_time")
        return candidate
    anchors = [by_id[sid] for sid in event.anchor_source_ids]
    expected = normalized_time(expression, anchors) if expression else None
    supported_zone = event.timezone is None or (
        len(anchors) == 1 and event.timezone == anchors[0].timezone
    )
    if (
        expected is not None
        and event.start == expected[0]
        and event.end in {None, expected[0]}
        and event.precision == expected[1]
        and supported_zone
    ):
        return candidate
    # Clearing metadata alone leaves the bad date in generated text. Fall back
    # to literal evidence too, so an unsupported normalization cannot leak there.
    data = candidate.model_dump()
    data["text"] = "\n".join(dict.fromkeys(e.quote for e in candidate.evidence))
    data["event_time"] = EventTime(
        original_expression=expression,
        start=None,
        end=None,
        precision="unknown",
        timezone=None,
        anchor_source_ids=event.anchor_source_ids,
    ).model_dump()
    data["uncertainties"] = [*candidate.uncertainties[:19], "time_normalization_unverified"]
    return Candidate.model_validate(data)
