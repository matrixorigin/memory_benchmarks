"""Deterministic wire-message rendering shared by the adapter and local audit."""

from datetime import UTC, datetime, timedelta

from .embedding_tokens import token_count
from .models import text_content

MAX_MEMORY_BYTES = 32768


def split_utf8(text: str, limit: int):
    if limit < 4:
        raise ValueError("UTF-8 part limit must be at least four bytes")
    remaining = text.encode("utf-8")
    while remaining:
        part = remaining[:limit].decode("utf-8", errors="ignore")
        yield part
        remaining = remaining[len(part.encode("utf-8")) :]


def split_tokens(text: str, header: str, budget: int):
    """Slice original Unicode, never decode normalized tokenizer output.

    Prefix token counts need not be monotonic. The binary search only selects a
    known-valid prefix; it need not find the largest possible one. Every emitted
    prefix is counted again with its header and special tokens.
    """
    offset = 0
    while offset < len(text):
        remaining = text[offset:]
        if token_count(header + remaining) <= budget:
            yield remaining
            return
        lo, hi = 0, len(remaining)
        while lo + 1 < hi:
            middle = (lo + hi) // 2
            if token_count(header + remaining[:middle]) <= budget:
                lo = middle
            else:
                hi = middle
        if lo == 0:
            raise ValueError("Token budget cannot fit the message prefix and a character")
        # Choose the latest nearby paragraph/word boundary, without dropping whitespace.
        floor = lo * 3 // 4
        boundary = max(remaining.rfind(mark, floor, lo) + 1 for mark in ("\n", " ", "。"))
        if boundary > floor and token_count(header + remaining[:boundary]) <= budget:
            lo = boundary
        yield remaining[:lo]
        offset += lo


def message_parts(message, *, token_budget: int | None):
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
        pieces = split_tokens(part, header, token_budget) if token_budget is not None else [part]
        for piece in pieces:
            yield header + piece, observed_at
