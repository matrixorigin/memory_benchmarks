"""Process-local admission and native-call budgets; use one replica and worker."""

import math
import time
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from threading import Event, Lock

import anyio

from .budget import remaining as budget_remaining
from .errors import AdapterError


def retry_after(depth: int, concurrency: int, service_seconds: float, rate_delay: float = 0) -> str:
    """A bounded congestion hint, not a promise of future available capacity."""
    capacity_delay = depth * service_seconds / concurrency if concurrency else 0
    return str(math.ceil(min(60, max(2, capacity_delay, rate_delay))))


class RequestAdmission:
    """Wait asynchronously before borrowing a dedicated business thread.

    Each admitted request owns one permit until its synchronous work completes.
    Matching this capacity to the business thread limiter prevents another
    unbounded wait inside AnyIO's shared default limiter.
    """

    def __init__(self, capacity: int, queue_timeout: float):
        self.capacity = capacity
        self.queue_timeout = queue_timeout
        self.permits = anyio.Semaphore(capacity)
        self.mean_service_seconds = 1.0

    @asynccontextmanager
    async def slot(self):
        try:
            with anyio.fail_after(budget_remaining(self.queue_timeout)):
                await self.permits.acquire()
        except TimeoutError as exc:
            budget_remaining(self.queue_timeout)
            depth = self.permits.statistics().tasks_waiting + self.capacity + 1
            raise AdapterError(
                503,
                "Adapter admission queue timed out; retry the same request later",
                write_not_sent=True,
                retry_after=retry_after(depth, self.capacity, self.mean_service_seconds),
            ) from exc
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - started
            self.mean_service_seconds = 0.8 * self.mean_service_seconds + 0.2 * elapsed
            self.permits.release()


class BackendScheduler:
    def __init__(self, concurrency: int, requests_per_second: float, queue_timeout: float):
        self.concurrency = concurrency
        self.interval = 1 / requests_per_second if requests_per_second else 0
        self.queue_timeout = queue_timeout
        self.lock = Lock()
        self.waiters: deque[Event] = deque()
        self.active = 0
        self.next_start = 0.0
        self.mean_service_seconds = 1.0

    def _has_capacity(self) -> bool:
        return not self.concurrency or self.active < self.concurrency

    def _wake_head(self) -> None:
        # Called under self.lock. Only the FIFO head needs a capacity/rate wakeup.
        if self.waiters and self._has_capacity():
            self.waiters[0].set()

    @contextmanager
    def slot(self, *, deadline: float | None = None):
        if not self.concurrency and not self.interval:
            budget_remaining(self.queue_timeout)
            yield
            return
        ticket = Event()
        deadline = min(
            time.monotonic() + budget_remaining(self.queue_timeout),
            deadline if deadline is not None else math.inf,
        )
        acquired = False
        started = 0.0
        with self.lock:
            self.waiters.append(ticket)
        try:
            while True:
                with self.lock:
                    now = time.monotonic()
                    remaining = deadline - now
                    if remaining <= 0:
                        budget_remaining(self.queue_timeout)
                        depth = len(self.waiters) + self.active
                        raise AdapterError(
                            503,
                            "Memoria queue timed out; retry the same request later",
                            write_not_sent=True,
                            retry_after=retry_after(
                                depth,
                                self.concurrency,
                                self.mean_service_seconds,
                                max(0, self.next_start - now) + depth * self.interval,
                            ),
                        )
                    is_head = self.waiters[0] is ticket
                    delay = self.next_start - now
                    if is_head and self._has_capacity() and delay <= 0:
                        self.waiters.popleft()
                        self.active += 1
                        acquired = True
                        started = now
                        self.next_start = now + self.interval
                        self._wake_head()
                        break
                    # Clearing under the lock preserves wakeups racing with wait().
                    ticket.clear()
                    wait = min(remaining, delay) if is_head and delay > 0 else remaining
                ticket.wait(wait)
            yield
        finally:
            with self.lock:
                if acquired:
                    self.active -= 1
                    elapsed = time.monotonic() - started
                    self.mean_service_seconds = 0.8 * self.mean_service_seconds + 0.2 * elapsed
                else:
                    self.waiters.remove(ticket)
                self._wake_head()
