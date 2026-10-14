"""Bound and pace native backend calls while synchronous API requests wait.

The scheduler is process-local. A deployment relying on a global budget must
use one adapter worker and one replica. Health probes use their own client.
"""

import time
from collections import deque
from contextlib import contextmanager
from threading import Condition

from .errors import AdapterError


class BackendScheduler:
    def __init__(self, concurrency: int, requests_per_second: float, queue_timeout: float):
        self.concurrency = concurrency
        self.interval = 1 / requests_per_second if requests_per_second else 0
        self.queue_timeout = queue_timeout
        self.condition = Condition()
        self.waiters: deque[object] = deque()
        self.active = 0
        self.next_start = 0.0

    @contextmanager
    def slot(self):
        if not self.concurrency and not self.interval:
            yield
            return
        ticket = object()
        deadline = time.monotonic() + self.queue_timeout
        with self.condition:
            self.waiters.append(ticket)
            try:
                while True:
                    now = time.monotonic()
                    remaining = deadline - now
                    if remaining <= 0:
                        raise AdapterError(
                            503,
                            "Memoria queue timed out; retry the same request later",
                            write_not_sent=True,
                            retry_after="1",
                        )
                    capacity = not self.concurrency or self.active < self.concurrency
                    delay = self.next_start - now
                    if self.waiters[0] is ticket and capacity and delay <= 0:
                        self.waiters.popleft()
                        self.active += 1
                        self.next_start = now + self.interval
                        self.condition.notify_all()
                        break
                    self.condition.wait(min(remaining, delay) if delay > 0 else remaining)
            except BaseException:
                self.waiters.remove(ticket)
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()
