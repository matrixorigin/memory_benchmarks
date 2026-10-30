import hmac
from contextlib import asynccontextmanager

import anyio
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .budget import request_budget
from .config import Settings
from .embedding_tokens import load_tokenizer
from .errors import AdapterError
from .journal import Journal
from .memoria import Memoria
from .models import AddRequest, AddResponse, SearchRequest, SearchResponse
from .scheduling import RequestAdmission
from .service import MemoryAdapter


def create_app(settings: Settings | None = None, *, client: httpx.Client | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    load_tokenizer()  # Fail startup rather than accept traffic with missing/wrong assets.
    http = client or httpx.Client(follow_redirects=False, trust_env=False)
    # Keep probe connections and thread capacity independent of long Add/Search calls.
    probe_http = client or httpx.Client(
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
    )
    probe_threads = anyio.CapacityLimiter(1)
    probe_gate = anyio.Semaphore(1)
    business_threads = anyio.CapacityLimiter(settings.max_active_requests)
    admission = RequestAdmission(
        settings.max_active_requests, settings.backend_queue_timeout_seconds
    )
    journal = Journal(
        settings.state_dir,
        log_all_operations=settings.journal_log_all_operations,
        slow_ms=settings.journal_slow_ms,
        queue_timeout_seconds=settings.journal_queue_timeout_seconds,
    )
    adapter = MemoryAdapter(settings, Memoria(settings, http), journal)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if client is None:
            http.close()
            probe_http.close()

    app = FastAPI(title="Memoria Leaderboard Adapter", lifespan=lifespan)

    @app.get("/health")
    async def health():
        """Unauthenticated adapter liveness; does not assert Memoria readiness."""
        return {"status": "ok"}

    def check_readiness() -> bool:
        try:
            adapter.journal.probe()
            response = probe_http.get(f"{settings.memoria_url}/health", timeout=3)
            return 200 <= response.status_code < 300
        except (AdapterError, httpx.HTTPError):
            return False

    @app.get("/ready")
    async def ready():
        """Check dependencies with one isolated probe per worker and no waiting queue."""
        try:
            probe_gate.acquire_nowait()
        except anyio.WouldBlock:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        try:
            if await anyio.to_thread.run_sync(check_readiness, limiter=probe_threads):
                return {"status": "ok"}
        finally:
            probe_gate.release()
        return JSONResponse(status_code=503, content={"status": "not_ready"})

    async def authenticate(request: Request) -> None:
        if settings.api_key is None:
            return
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        candidates = [request.headers.get("X-Api-Key", "")]
        if scheme.lower() in {"bearer", "token"}:
            candidates.append(token)
        if not any(
            hmac.compare_digest(candidate.encode(), settings.api_key.encode())
            for candidate in candidates
        ):
            raise AdapterError(401, "Invalid or missing adapter API key")

    @app.exception_handler(AdapterError)
    async def adapter_error(request: Request, error: AdapterError):
        detail = {"reason": error.reason}
        if error.status == 422:
            detail = [{"loc": ["body"], "msg": error.reason, "type": "value_error"}]
        headers = {"Retry-After": error.retry_after} if error.retry_after is not None else None
        return JSONResponse(status_code=error.status, content={"detail": detail}, headers=headers)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        # Framework errors (including unknown routes) use the same public envelope.
        reason = {
            400: "Request could not be parsed; check the body and Content-Type",
            404: "Endpoint not found; check the URL",
            405: "Method not allowed; Add and Search require POST",
        }.get(error.status_code, "HTTP request failed; check the status code")
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": {"reason": reason}},
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": {"reason": "Internal adapter error; retry and check server logs"}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Pydantic's default includes input values; avoid echoing entire histories.
        issues = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail": issues})

    @app.post("/add", response_model=AddResponse, dependencies=[Depends(authenticate)])
    async def add(request: AddRequest):
        with request_budget(settings.request_timeout_seconds):
            async with admission.slot():
                # Keep admission until the write thread finishes and records its outcome.
                return await anyio.to_thread.run_sync(
                    adapter.add, request, limiter=business_threads
                )

    @app.post(
        "/search",
        response_model=SearchResponse,
        response_model_exclude_none=True,
        dependencies=[Depends(authenticate)],
    )
    async def search(request: SearchRequest):
        with request_budget(settings.request_timeout_seconds):
            async with admission.slot():
                return await anyio.to_thread.run_sync(
                    adapter.search, request, limiter=business_threads
                )

    return app
