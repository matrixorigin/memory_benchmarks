"""Deterministic wire-message rendering shared by the adapter and local audit."""

from datetime import UTC, datetime, timedelta

from .models import text_content

RENDER_VERSION = 1
MAX_MEMORY_BYTES = 32768


def split_utf8(text: str, limit: int):
    if limit < 4:
        raise ValueError("UTF-8 part limit must be at least four bytes")
    remaining = text.encode("utf-8")
    while remaining:
        part = remaining[:limit].decode("utf-8", errors="ignore")
        yield part
        remaining = remaining[len(part.encode("utf-8")) :]


def message_parts(message):
    observed_at = None
    header = f"[{message.role}]\n"
    if message.timestamp is not None:
        observed_at = (
            (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=message.timestamp))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        header = f"[{message.role} at {observed_at}]\n"
    for part in split_utf8(text_content(message.content), MAX_MEMORY_BYTES - len(header.encode())):
        yield header + part, observed_at
