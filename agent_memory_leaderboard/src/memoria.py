"""HTTP boundary: use an owner-scoped master credential, never caller headers."""

import logging
import math
import re
import time
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .budget import CATALOG_RETRY_WINDOW, CatalogRetryWindow
from .budget import remaining as budget_remaining
from .config import Settings
from .errors import AdapterError, CatalogVisibilityPending, VisibilityPending
from .models import Nonblank
from .scheduling import BackendScheduler

IDEMPOTENT_PATH = "/v1/memories/idempotent"
ADAPTER_BRANCH = "main"
_TABLE_IDENTIFIER = r'''["'`]*(?:[\w-]+["'`]*\.)?["'`]*mem_[a-z0-9_]+["'`]*'''
_MISSING_TABLE = re.compile(
    rf"\b1064\s*\(42000\):\s*SQL parser error:\s*table\s+{_TABLE_IDENTIFIER}"
    rf"\s+does not exist\b|\b1146\s*\(42S02\):\s*no such table\s+"
    rf"{_TABLE_IDENTIFIER}(?=\s|[,;:)]|$)",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)


class Memory(BaseModel):
    model_config = ConfigDict(strict=True)
    memory_id: Nonblank
    user_id: str
    content: Nonblank
    is_active: bool
    extra_metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_score: Annotated[float, Field(allow_inf_nan=False)] | None = None
    created_at: str | None = None


class Memoria:
    def __init__(self, settings: Settings, client: httpx.Client):
        self.settings = settings
        self.client = client
        self.scheduler = BackendScheduler(
            settings.backend_max_concurrency,
            settings.backend_requests_per_second,
            settings.backend_queue_timeout_seconds,
        )

    def _visibility_timeout(self) -> AdapterError:
        delay = str(max(1, min(60, math.ceil(self.settings.visibility_timeout_seconds))))
        return AdapterError(503, "Memoria write visibility timed out; retry Add", retry_after=delay)

    def _remaining_visibility(self, deadline: float) -> float:
        budget_remaining(float("inf"))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._visibility_timeout()
        return remaining

    def request(
        self,
        method: str,
        path: str,
        owner: str,
        body: dict | None = None,
        *,
        deadline: float | None = None,
    ) -> Any:
        # Replay only reads and the atomic write route, preserving the exact
        # payload/key. A failed response does not prove a write was uncommitted.
        safe_replay = method == "GET" or (
            method == "POST"
            and (
                path in {"/v1/memories/query", "/v1/memories/search"}
                or (path == IDEMPOTENT_PATH and body and body.get("idempotency_key"))
            )
        )
        window = CATALOG_RETRY_WINDOW.get()
        if window is None:
            window = CatalogRetryWindow(self.settings.catalog_retry_timeout_seconds)
        retry_deadline = None
        attempt = 0
        while True:
            try:
                result = self._request_once(
                    method, path, owner, body, deadline=deadline, retry_deadline=retry_deadline
                )
                if retry_deadline is not None and time.monotonic() >= retry_deadline:
                    budget_remaining(float("inf"))
                    if deadline is not None:
                        self._remaining_visibility(deadline)
                    raise CatalogVisibilityPending(502, "Memoria catalog retry window expired")
                return result
            except CatalogVisibilityPending:
                if not safe_replay:
                    raise
                retry_deadline = window.start()
                available = budget_remaining(retry_deadline - time.monotonic())
                if deadline is not None:
                    available = min(available, self._remaining_visibility(deadline))
                if available <= 0:
                    raise
                delay = min(0.5 if attempt == 0 else 1.0, available)
                # No upstream body, URL, owner, credentials or payload in logs.
                logger.warning(
                    "Memoria catalog visibility retry wait: attempt=%d delay_ms=%.0f",
                    attempt + 1,
                    delay * 1000,
                )
                # _request_once released the backend slot before this wait.
                time.sleep(delay)
                attempt += 1
                budget_remaining(float("inf"))
                if deadline is not None:
                    self._remaining_visibility(deadline)
                if time.monotonic() >= retry_deadline:
                    raise
            except AdapterError as exc:
                # Earlier attempts were ambiguous even if this retry never sent.
                if retry_deadline is not None:
                    exc.write_not_sent = False
                raise

    def _request_once(
        self,
        method: str,
        path: str,
        owner: str,
        body: dict | None = None,
        *,
        deadline: float | None = None,
        retry_deadline: float | None = None,
    ) -> Any:
        budget_remaining(float("inf"))
        if deadline is not None:
            self._remaining_visibility(deadline)
        visibility_limited = False
        try:
            admission_deadline = min(
                deadline if deadline is not None else math.inf,
                retry_deadline if retry_deadline is not None else math.inf,
            )
            with self.scheduler.slot(deadline=admission_deadline):
                timeout = budget_remaining(self.settings.timeout_seconds)
                if retry_deadline is not None:
                    retry_remaining = retry_deadline - time.monotonic()
                    if retry_remaining <= 0:
                        raise CatalogVisibilityPending(502, "Memoria catalog retry window expired")
                    timeout = min(timeout, retry_remaining)
                if deadline is not None:
                    remaining = self._remaining_visibility(deadline)
                    visibility_limited = remaining <= timeout
                    timeout = min(timeout, remaining)
                response = self.client.request(
                    method,
                    self.settings.memoria_url.rstrip("/") + path,
                    headers={
                        "Authorization": f"Memoria-Owner {self.settings.memoria_master_key}",
                        "X-User-Id": owner,
                    },
                    json=body,
                    timeout=httpx.Timeout(
                        timeout,
                        connect=min(self.settings.connect_timeout_seconds, timeout),
                        pool=min(self.settings.connect_timeout_seconds, timeout),
                    ),
                )
        except httpx.TimeoutException as exc:
            budget_remaining(float("inf"))
            if deadline is not None and (visibility_limited or time.monotonic() >= deadline):
                raise self._visibility_timeout() from exc
            if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)):
                raise AdapterError(
                    503, "Memoria connection unavailable", write_not_sent=True
                ) from exc
            raise AdapterError(504, "Memoria request timed out") from exc
        except httpx.ConnectError as exc:
            raise AdapterError(503, "Memoria connection unavailable", write_not_sent=True) from exc
        except httpx.RequestError as exc:
            raise AdapterError(502, "Memoria transport failed") from exc

        if response.status_code not in {200, 201}:
            # Do not relay upstream bodies: they can contain content or credentials.
            code = response.status_code
            status, reason = {
                400: (400, "Memoria rejected the payload; review the request content"),
                401: (502, "Memoria authentication failed; check the backend credential"),
                403: (403, "Memoria denied the request; review user scope and content policy"),
                404: (502, "Memoria endpoint not found; check the backend URL and API version"),
                405: (502, "Memoria method unsupported; check the backend API version"),
                408: (408, "Memoria request timed out; retry the same request"),
                409: (409, "Memoria reported a state conflict"),
                413: (422, "Memoria rejected the payload size; review backend limits"),
                415: (422, "Memoria rejected the payload format; review backend requirements"),
                422: (422, "Memoria rejected the payload; review content and backend limits"),
                425: (425, "Memoria is not ready; retry the same request later"),
                429: (429, "Memoria capacity exceeded; retry the same request later"),
                503: (503, "Memoria is unavailable; retry the same request later"),
                504: (504, "Memoria request timed out; retry the same request"),
                524: (504, "Memoria request timed out; retry the same request"),
            }.get(
                code, (502, f"Memoria returned HTTP {code}; check backend health and API version")
            )
            if path == IDEMPOTENT_PATH and code in {404, 405}:
                reason = "Memoria atomic writes unsupported; upgrade backend before retrying Add"
            # Admission/validation rejections cannot commit a write. Timeouts,
            # conflicts and server errors remain ambiguous and require recovery.
            rejected = code in {400, 401, 403, 404, 405, 413, 415, 422, 425, 429}
            retry_after = response.headers.get("Retry-After", "")
            error_class = (
                CatalogVisibilityPending
                if code == 500 and _MISSING_TABLE.search(response.text[:16384])
                else AdapterError
            )
            raise error_class(
                status,
                reason,
                write_not_sent=rejected,
                # Forward only bounded, numeric delay hints, never arbitrary headers.
                retry_after=(
                    str(min(int(retry_after), 60))
                    if code in {429, 503}
                    and retry_after.isascii()
                    and retry_after.isdigit()
                    and len(retry_after) <= 10
                    else None
                ),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AdapterError(502, "Memoria returned invalid JSON") from exc

    @staticmethod
    def memory(data: Any, owner: str) -> Memory:
        try:
            result = Memory.model_validate(data)
        except ValidationError as exc:
            raise AdapterError(502, "Memoria returned an invalid memory") from exc
        if result.user_id != owner:
            raise AdapterError(502, "Memoria returned a memory outside the requested user scope")
        if not result.is_active:
            raise AdapterError(503, "Memoria memory is not active")
        return result

    def store_idempotent(self, owner: str, body: dict, marker: str) -> Memory:
        # The dedicated route fails safely on old servers. Never fall back to
        # ordinary POST, where an unknown JSON field could be silently ignored.
        payload = {**body, "idempotency_key": marker, "branch": ADAPTER_BRANCH}
        return self.memory(self.request("POST", IDEMPOTENT_PATH, owner, payload), owner)

    def get(self, owner: str, memory_id: str, *, deadline: float | None = None) -> Memory:
        path = f"/v1/memories/{quote(memory_id, safe='')}?branch={ADAPTER_BRANCH}"
        data = self.request("GET", path, owner, deadline=deadline)
        if data is None:
            raise VisibilityPending(503, "Memoria write is not yet readable")
        memory = self.memory(data, owner)
        if memory.memory_id != memory_id:
            raise AdapterError(502, "Memoria returned an unexpected memory ID")
        return memory

    def recover(self, owner: str, marker: str) -> Memory | None:
        data = self.request(
            "POST",
            "/v1/memories/query",
            owner,
            {
                "extra_metadata_filter": {"aml_write": marker},
                "limit": 2,
                "branch": ADAPTER_BRANCH,
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise AdapterError(502, "Memoria returned an invalid recovery response")
        items = data["items"]
        if len(items) > 1 or data.get("next_cursor") is not None:
            raise AdapterError(502, "Memoria has duplicate records for one write")
        if not items:
            return None
        memory = self.memory(items[0], owner)
        if memory.extra_metadata.get("aml_write") != marker:
            raise AdapterError(502, "Memoria recovery returned an unexpected record")
        return memory

    def search(self, owner: str, query: str, top_k: int) -> list[Memory]:
        # No session filter: the user boundary includes all of their sessions.
        data = self.request(
            "POST",
            "/v1/memories/search",
            owner,
            {"query": query, "top_k": min(top_k, 100), "branch": ADAPTER_BRANCH},
        )
        if not isinstance(data, list):
            raise AdapterError(502, "Memoria returned an invalid search response")
        return [self.memory(item, owner) for item in data]

    def verify_searchable(
        self,
        owner: str,
        memory: Memory,
        marker: str,
        *,
        deadline: float | None = None,
    ) -> None:
        data = self.request(
            "POST",
            "/v1/memories/search",
            owner,
            {
                "query": memory.content,
                "top_k": 100,
                "subject_id": marker,
                "branch": ADAPTER_BRANCH,
            },
            deadline=deadline,
        )
        if not isinstance(data, list):
            raise AdapterError(502, "Memoria returned an invalid visibility response")
        results = [self.memory(item, owner) for item in data]
        matching = next((item for item in results if item.memory_id == memory.memory_id), None)
        if matching is None:
            raise VisibilityPending(
                503, "Memoria write is persisted but not yet searchable; retry Add"
            )
        if matching.extra_metadata.get("aml_write") != marker:
            raise AdapterError(502, "Memoria search returned an unexpected write marker")

    def wait_for_visibility(self, owner: str, memory_id: str, marker: str) -> Memory:
        # One polling budget covers point-read and retrieval verification. Never
        # repeat a write here; a known ID remains recoverable on a later Add.
        deadline = time.monotonic() + self.settings.visibility_timeout_seconds
        delay = 0.05
        persisted = None
        while True:
            self._remaining_visibility(deadline)
            try:
                if persisted is None:
                    persisted = self.get(owner, memory_id, deadline=deadline)
                    if persisted.extra_metadata.get("aml_write") != marker:
                        raise AdapterError(502, "Memoria did not persist the write marker")
                self.verify_searchable(owner, persisted, marker, deadline=deadline)
                # A slow in-flight call must not report success after expiry.
                self._remaining_visibility(deadline)
                return persisted
            except VisibilityPending:
                remaining = self._remaining_visibility(deadline)
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.5)
