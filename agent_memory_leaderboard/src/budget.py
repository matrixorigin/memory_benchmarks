"""Cooperative request budget shared by admission, journal and backend work.

An in-flight synchronous I/O or commit is allowed to unwind safely. This is not
an asynchronous hard kill and does not claim a strict HTTP wall-clock deadline.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .errors import AdapterError

DEADLINE: ContextVar[float | None] = ContextVar("adapter_request_deadline", default=None)


@dataclass
class CatalogRetryWindow:
    seconds: float
    deadline: float | None = None

    def start(self) -> float:
        if self.deadline is None:
            self.deadline = time.monotonic() + self.seconds
        return self.deadline


CATALOG_RETRY_WINDOW: ContextVar[CatalogRetryWindow | None] = ContextVar(
    "adapter_catalog_retry_window", default=None
)


@contextmanager
def catalog_retry_budget(seconds: float):
    """One lazy window shared by all backend calls/chunks in this request."""
    parent = CATALOG_RETRY_WINDOW.get()
    token = CATALOG_RETRY_WINDOW.set(parent if parent is not None else CatalogRetryWindow(seconds))
    try:
        yield
    finally:
        CATALOG_RETRY_WINDOW.reset(token)


class RequestBudgetExceeded(AdapterError):
    def __init__(self):
        super().__init__(
            504, "Adapter request budget exhausted; retry the same request", retry_after="2"
        )


def remaining(limit: float) -> float:
    deadline = DEADLINE.get()
    if deadline is None:
        return limit
    available = deadline - time.monotonic()
    if available <= 0:
        raise RequestBudgetExceeded()
    return min(limit, available)


@contextmanager
def request_budget(seconds: float):
    parent = DEADLINE.get()
    deadline = time.monotonic() + seconds
    token = DEADLINE.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        DEADLINE.reset(token)
