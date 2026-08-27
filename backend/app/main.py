from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import (
    auth,
    backups,
    decision,
    instruments,
    jobs,
    market,
    research,
    research_history,
    scans,
    settings,
    system,
    watchlists,
)
from backend.app.core.config import PROJECT_ROOT, get_settings
from backend.app.core.exception_handlers import register_exception_handlers
from backend.app.core.logging import configure_logging
from backend.app.core.security import cleanup_expired_sessions, ensure_identity
from backend.app.db.session import SessionLocal, engine
from backend.app.services.secrets import SecretService

settings_value = get_settings()
configure_logging(settings_value.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with SessionLocal() as session:
        ensure_identity(session, settings_value)
        cleanup_expired_sessions(session)
        SecretService(session, settings_value).apply_to_environment()
    logger.info("ChanAnalyzer API started")
    yield
    engine.dispose()
    logger.info("ChanAnalyzer API stopped")


app = FastAPI(
    title="ChanAnalyzer API",
    description="A股缠论研究工作台 v2",
    version=settings_value.app_version,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings_value.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID", "X-Requested-With", "Last-Event-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    if (
        settings_value.auth_mode == "admin"
        and request.url.path.startswith("/api/")
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
    ):
        origin = request.headers.get("Origin")
        if origin not in settings_value.allowed_origins:
            return JSONResponse(
                status_code=403,
                content={
                    "code": "origin_rejected",
                    "message": "请求来源不受信任",
                    "request_id": request_id,
                },
                headers={"X-Request-ID": request_id},
            )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if settings_value.environment == "server":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    logger.exception("Unhandled API error", extra={"request_id": request_id})
    payload = {"code": "internal_error", "message": "服务内部错误", "request_id": request_id}
    return JSONResponse(status_code=500, content=payload)


register_exception_handlers(app)

api_prefix = "/api/v1"
app.include_router(auth.router, prefix=api_prefix)
app.include_router(backups.router, prefix=api_prefix)
app.include_router(instruments.router, prefix=api_prefix)
app.include_router(decision.router, prefix=api_prefix)
app.include_router(jobs.router, prefix=api_prefix)
app.include_router(market.router, prefix=api_prefix)
app.include_router(research.router, prefix=api_prefix)
app.include_router(research_history.router, prefix=api_prefix)
app.include_router(scans.router, prefix=api_prefix)
app.include_router(settings.router, prefix=api_prefix)
app.include_router(system.router, prefix=api_prefix)
app.include_router(watchlists.router, prefix=api_prefix)


@app.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready", include_in_schema=False)
def ready() -> dict[str, str]:
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    return {"status": "ready"}


frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.exists():
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        candidate = (frontend_dist / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(frontend_dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")


def run() -> None:
    uvicorn.run(
        "backend.app.main:app", host=settings_value.api_host, port=settings_value.api_port, reload=False
    )


if __name__ == "__main__":
    run()
