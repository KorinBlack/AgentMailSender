from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import admin, agent, auth
from .config import Config
from .context import AppContext
from .errors import ApiError

STATIC_DIR = Path(__file__).parent / "static"

APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
    "object-src 'none'"
)

API_DESCRIPTION = """
Agents submit e-mails here; **nothing is sent until a human operator approves it** in the web dashboard.

**Authentication** — every call needs the API key of the agent's profile:
`Authorization: Bearer ams_…` (or `X-API-Key: ams_…`). Keys are created in the dashboard under *Profiles*.

**Workflow** — `POST /api/v1/mails` returns `202` with a request in status `pending`. Poll
`GET /api/v1/mails/{id}` (or list with `updated_since`) to learn the outcome: `sent`, `rejected`
(read `decision_comment` for the operator's feedback), `failed` or `cancelled`.
"""


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Reject request bodies above a size limit, with or without a Content-Length header."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers") or []:
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                await self._reject(send)
                return
        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send):
        response = JSONResponse(
            {"detail": f"Request body too large (limit {self.max_bytes // 1048576} MB).", "code": "body_too_large"},
            status_code=413,
        )
        await response({"type": "http"}, None, send)


def create_app(config: Config | None = None, *, context: AppContext | None = None) -> FastAPI:
    ctx = context or AppContext(config or Config.from_env())
    ctx.init_db()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        ctx.close()

    app = FastAPI(
        title="AgentMailSender",
        version=__version__,
        description=API_DESCRIPTION,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.ctx = ctx

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 and request.url.path.startswith("/api/v1") else None
        return JSONResponse(exc.to_dict(), status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            errors.append({"field": loc, "message": err.get("msg", "Invalid value")})
        summary = "; ".join(f"{e['field']}: {e['message']}" if e["field"] else e["message"] for e in errors)
        return JSONResponse(
            {"detail": f"Invalid request: {summary}", "code": "validation_error", "errors": errors}, status_code=422
        )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if not path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy", APP_CSP)
            response.headers.setdefault("X-Frame-Options", "DENY")
        if path == "/" or path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        elif path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=ctx.config.max_body_mb * 1024 * 1024)

    app.include_router(agent.router)
    app.include_router(auth.router)
    app.include_router(admin.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    logging.getLogger("agentmailsender").debug("Application ready (data dir: %s)", ctx.config.data_dir)
    return app
