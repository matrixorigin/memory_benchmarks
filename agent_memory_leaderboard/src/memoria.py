"""HTTP boundary: use an owner-scoped master credential, never caller headers."""

from typing import Annotated, Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import Settings
from .errors import AdapterError
from .models import Nonblank
from .scheduling import BackendScheduler


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

    def request(self, method: str, path: str, owner: str, body: dict | None = None) -> Any:
        try:
            with self.scheduler.slot():
                response = self.client.request(
                    method,
                    self.settings.memoria_url.rstrip("/") + path,
                    headers={
                        "Authorization": f"Memoria-Owner {self.settings.memoria_master_key}",
                        "X-User-Id": owner,
                    },
                    json=body,
                    timeout=self.settings.timeout_seconds,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise AdapterError(503, "Memoria connection unavailable", write_not_sent=True) from exc
        except httpx.TimeoutException as exc:
            raise AdapterError(504, "Memoria request timed out") from exc
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
            # Admission/validation rejections cannot commit a write. Timeouts,
            # conflicts and server errors remain ambiguous and require recovery.
            rejected = code in {400, 401, 403, 404, 405, 413, 415, 422, 425, 429}
            retry_after = response.headers.get("Retry-After", "")
            raise AdapterError(
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

    def store(self, owner: str, body: dict) -> Memory:
        return self.memory(self.request("POST", "/v1/memories", owner, body), owner)

    def get(self, owner: str, memory_id: str) -> Memory:
        data = self.request("GET", f"/v1/memories/{quote(memory_id, safe='')}", owner)
        if data is None:
            raise AdapterError(503, "Memoria write is not yet readable")
        memory = self.memory(data, owner)
        if memory.memory_id != memory_id:
            raise AdapterError(502, "Memoria returned an unexpected memory ID")
        return memory

    def recover(self, owner: str, marker: str) -> Memory | None:
        data = self.request(
            "POST",
            "/v1/memories/query",
            owner,
            {"extra_metadata_filter": {"aml_write": marker}, "limit": 2},
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
            "POST", "/v1/memories/search", owner, {"query": query, "top_k": min(top_k, 100)}
        )
        if not isinstance(data, list):
            raise AdapterError(502, "Memoria returned an invalid search response")
        return [self.memory(item, owner) for item in data]

    def verify_searchable(self, owner: str, memory: Memory, marker: str) -> None:
        data = self.request(
            "POST",
            "/v1/memories/search",
            owner,
            {"query": memory.content, "top_k": 100, "subject_id": marker},
        )
        if not isinstance(data, list):
            raise AdapterError(502, "Memoria returned an invalid visibility response")
        results = [self.memory(item, owner) for item in data]
        if not any(item.memory_id == memory.memory_id for item in results):
            raise AdapterError(503, "Memoria write is persisted but not yet searchable; retry Add")
