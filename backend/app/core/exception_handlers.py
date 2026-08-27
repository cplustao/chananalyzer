from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.app.core.errors import AppError, safe_validation_errors

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details=None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    payload = {"code": code, "message": message, "request_id": _request_id(request)}
    if details is not None:
        payload["details"] = details
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return _response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "请求未完成"
        details = None if isinstance(exc.detail, str) else exc.detail
        return _response(
            request,
            status_code=exc.status_code,
            code="http_error",
            message=message,
            details=details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _response(
            request,
            status_code=422,
            code="validation_error",
            message="请求参数校验失败",
            details=safe_validation_errors(exc.errors()),
        )

    @app.exception_handler(Exception)
    async def unknown_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.exception("Unhandled API error", extra={"request_id": request_id})
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "服务内部错误", "request_id": request_id},
        )
