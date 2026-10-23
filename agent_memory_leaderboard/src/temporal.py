"""Bounded, opt-in calendar hints attached to original evidence, without an LLM.

These are explicit interpretation conventions, not inferred event facts. Never
use ingestion/creation time or the wall clock as a missing message-time anchor.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

VERSION = "calendar-hints-v1"
MAX_RESPONSE_CHARS = 8192
MAX_RECORD_CHARS = 1024
MONTHS = {
    name: i + 1
    for i, name in enumerate(
        (
            "January February March April May June July August September October November December"
        ).split()
    )
}
WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday".split()
HEADER = re.compile(r"^\[(?:user|assistant)(?: at ([^\]\n]+))?\]\n")
BENCHMARK_TIME = re.compile(
    r"^\[(\d{1,2}):(\d{2}) (am|pm) on (\d{1,2}) ([A-Za-z]+), (\d{4})\] ", re.I
)
EXPRESSION = re.compile(
    r"\b(?:yesterday|today|tomorrow|\d{1,3} days? ago|"
    r"(?:last|this|next) (?:week|month|year|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday))\b"
    r"|昨天|今天|明天|(?<!\d)\d{1,3}天前|[上本下]周[一二三四五六日天]?(?!末)|[上本下]个月|去年|今年|明年",
    re.I,
)


def _iso(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.date()
    except (ValueError, TypeError):
        return None


def _anchor(content, metadata):
    # Continuation chunks may start in a quotation or contain a different message.
    if metadata.get("part_index", 0) != 0:
        return None, ""
    header = HEADER.match(content)
    body = content[header.end() :] if header else content
    anchors = []
    stamp = metadata.get("timestamp")
    if stamp is not None:
        if type(stamp) is not int:
            return None, ""
        try:
            anchors.append(
                (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=stamp)).date()
            )
        except (ValueError, OverflowError):
            return None, ""
    if header and header[1]:
        parsed = _iso(header[1])
        if parsed is None:
            return None, ""
        anchors.append(parsed)
    benchmark = BENCHMARK_TIME.match(body)
    if benchmark:
        hour, minute, _, day, month, year = benchmark.groups()
        try:
            if not 1 <= int(hour) <= 12 or not 0 <= int(minute) <= 59:
                return None, ""
            anchors.append(date(int(year), MONTHS[month.title()], int(day)))
        except (ValueError, KeyError):
            return None, ""
        body = body[benchmark.end() :]
    if not anchors or any(value != anchors[0] for value in anchors):
        return None, ""
    # Multiple message timestamps are not a single anchored utterance.
    if re.search(r"\[\d{1,2}:\d{2} (?:am|pm) on ", body, re.I):
        return None, ""
    return anchors[0], body


def _resolve(expression, anchor):
    value = expression.lower()
    days = {"yesterday": -1, "today": 0, "tomorrow": 1, "昨天": -1, "今天": 0, "明天": 1}
    if value in days:
        return (anchor + timedelta(days=days[value])).isoformat(), "relative-day"
    ago = re.fullmatch(r"(\d{1,3})(?: days? ago|天前)", value)
    if ago:
        return (anchor - timedelta(days=int(ago[1]))).isoformat(), "relative-day"
    weekday = re.fullmatch(r"(last|this|next) (" + "|".join(WEEKDAYS) + r")", value)
    if weekday:
        direction, name = weekday.groups()
        target = WEEKDAYS.index(name)
        if direction == "last":
            offset = -((anchor.weekday() - target) % 7 or 7)
            rule = "last-weekday=nearest-strictly-earlier"
        elif direction == "next":
            offset = (target - anchor.weekday()) % 7 or 7
            rule = "next-weekday=nearest-strictly-later"
        else:
            offset = target - anchor.weekday()
            rule = "this-weekday=calendar-week-Mon-Sun"
        return (anchor + timedelta(days=offset)).isoformat(), rule
    chinese_week = re.fullmatch(r"([上本下])周([一二三四五六日天])?", value)
    week = re.fullmatch(r"(last|this|next) week", value)
    if chinese_week or week:
        shift = (
            {"上": -1, "本": 0, "下": 1}[chinese_week[1]]
            if chinese_week
            else {"last": -1, "this": 0, "next": 1}[week[1]]
        )
        start = anchor + timedelta(days=-anchor.weekday() + 7 * shift)
        if chinese_week and chinese_week[2]:
            day = "一二三四五六日".index(chinese_week[2].replace("天", "日"))
            return (start + timedelta(days=day)).isoformat(), "calendar-week-Mon-Sun"
        return (
            f"{start.isoformat()}..{(start + timedelta(days=6)).isoformat()}",
            "calendar-week-Mon-Sun",
        )
    month = re.fullmatch(r"(last|this|next) month", value)
    chinese_month = re.fullmatch(r"([上本下])个月", value)
    if month or chinese_month:
        shift = (
            {"last": -1, "this": 0, "next": 1}[month[1]]
            if month
            else {"上": -1, "本": 0, "下": 1}[chinese_month[1]]
        )
        year, zero_month = divmod(anchor.year * 12 + anchor.month - 1 + shift, 12)
        date(year, zero_month + 1, 1)  # Validate year bounds before formatting.
        return f"{year:04d}-{zero_month + 1:02d}", "calendar-month"
    year = re.fullmatch(r"(last|this|next) year", value)
    if year or value in {"去年", "今年", "明年"}:
        shift = (
            {"last": -1, "this": 0, "next": 1}[year[1]]
            if year
            else {"去年": -1, "今年": 0, "明年": 1}[value]
        )
        result = anchor.year + shift
        date(result, 1, 1)
        return f"{result:04d}", "calendar-year"
    return None


def _outside_quotes(body):
    # Mask direct quotations instead of assigning their relative times to the
    # surrounding utterance. Leave apostrophes in contractions alone. Unclosed
    # double/curly quotation marks conservatively mask the rest of the message.
    chars = list(body)
    closing = None
    pairs = {'"': '"', "“": "”", "‘": "’", "「": "」", "『": "』"}
    for index, char in enumerate(body):
        if closing:
            chars[index] = " "
            if char == closing:
                closing = None
        elif char in pairs:
            closing = pairs[char]
            chars[index] = " "
        elif char == "'" and (index == 0 or not body[index - 1].isalnum()):
            closing = "'"
            chars[index] = " "
    return "".join(chars)


def annotate(content: str, metadata: dict | None = None, *, budget: int = MAX_RECORD_CHARS) -> str:
    """Append optional hints, never rewrite/truncate the original content."""
    if budget <= 0:
        return content
    anchor, body = _anchor(content, metadata or {})
    if anchor is None:
        return content
    hints = []
    seen = set()
    for match in EXPRESSION.finditer(_outside_quotes(body)):
        expression = body[match.start() : match.end()]
        if expression.lower() in seen:
            continue
        seen.add(expression.lower())
        try:
            resolved = _resolve(expression, anchor)
        except (ValueError, OverflowError):
            continue
        if resolved is None:
            continue
        hints.append(
            {"expression": expression, "calendar_value": resolved[0], "convention": resolved[1]}
        )
        block = "\n[Calendar hints; original text above is authoritative; " + VERSION + "]\n"
        block += json.dumps(
            {"message_date": anchor.isoformat(), "hints": hints}, ensure_ascii=False
        )
        if len(block) > min(budget, MAX_RECORD_CHARS):
            hints.pop()
            break
        if len(hints) == 8:
            break
    if not hints:
        return content
    block = "\n[Calendar hints; original text above is authoritative; " + VERSION + "]\n"
    return (
        content
        + block
        + json.dumps({"message_date": anchor.isoformat(), "hints": hints}, ensure_ascii=False)
    )
