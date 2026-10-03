"""The Textual/Coding wire contract from the API guide."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("must contain valid Unicode characters") from exc
    return value  # IDs and evidence must retain their original whitespace.


Nonblank = Annotated[str, AfterValidator(nonblank)]


class WireModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class TextPart(WireModel):
    type: Literal["text"]
    text: Nonblank


# Image parts deliberately fail validation: this adapter serves Textual/Coding.
Content = Nonblank | Annotated[list[TextPart], Field(min_length=1)]


def text_content(content: Content) -> str:
    return content if isinstance(content, str) else "\n".join(part.text for part in content)


def valid_timestamp(value: int) -> int:
    try:
        datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)
    except OverflowError as exc:
        raise ValueError("timestamp must be representable as a UTC datetime") from exc
    return value


class Message(WireModel):
    role: Literal["user", "assistant"]
    content: Content
    timestamp: Annotated[StrictInt, AfterValidator(valid_timestamp)] | None = None


class AddRequest(WireModel):
    request_id: Nonblank
    user_id: Nonblank
    session_id: Nonblank
    messages: Annotated[list[Message], Field(min_length=1)]


class AddResponse(WireModel):
    success: Literal[True] = True
    request_id: str
    user_id: str
    session_id: str


class SearchRequest(WireModel):
    query: Content
    user_id: Nonblank
    top_k: Annotated[StrictInt, Field(ge=1)]
    options: list[str] | None = None


class Evidence(WireModel):
    id: Nonblank
    content: Nonblank
    score: Annotated[float, Field(allow_inf_nan=False)] | None = None
    created_at: str | None = None


class SearchResponse(WireModel):
    data: list[Evidence]
