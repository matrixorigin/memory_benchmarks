import hmac
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .config import Settings
from .errors import AdapterError
from .journal import Journal
from .memoria import Memoria
from .models import AddRequest, AddResponse, SearchRequest, SearchResponse
from .service import MemoryAdapter


def create_app(settings: Settings | None = None, *, client: httpx.Client | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    http = client or httpx.Client(follow_redirects=False, trust_env=False)
    adapter = MemoryAdapter(settings, Memoria(settings, http), Journal(settings.state_dir))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if client is None:
            http.close()

    app = FastAPI(title="Memoria Leaderboard Adapter", lifespan=lifespan)

    @app.get("/health")
    def health():
        """Unauthenticated adapter liveness; does not assert Memoria readiness."""
        return {"status": "ok"}

    def authenticate(request: Request) -> None:
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
    def add(request: AddRequest):
        return adapter.add(request)

    @app.post(
        "/search",
        response_model=SearchResponse,
        response_model_exclude_none=True,
        dependencies=[Depends(authenticate)],
    )
    def search(request: SearchRequest):
        return adapter.search(request)

    return app
