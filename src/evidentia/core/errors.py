"""Typed application errors rendered as RFC 9457 problem details."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"

log = structlog.get_logger(__name__)


class AppError(Exception):
    """Base for errors that map cleanly onto an HTTP response."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    title: str = "Internal server error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.detail = detail or self.title
        self.headers = headers
        if code:
            self.code = code
        self.extra = extra or {}
        super().__init__(self.detail)


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    title = "Bad request"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    title = "Authentication required"

    def __init__(self, detail: str | None = None, *, code: str | None = None) -> None:
        super().__init__(detail, code=code, headers={"WWW-Authenticate": "Bearer"})


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    title = "Not allowed"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    title = "Resource not found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    title = "Conflict"


class UnprocessableError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "unprocessable"
    title = "Unprocessable request"


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "too_many_requests"
    title = "Too many requests"


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    title = "Service temporarily unavailable"


class UpstreamError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_error"
    title = "Upstream dependency failed"


def _problem(
    request: Request,
    *,
    status_code: int,
    title: str,
    detail: str,
    code: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"about:blank#{code}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
    }
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status_code, media_type=PROBLEM_JSON, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("app_error", code=exc.code, detail=exc.detail)
        return _problem(
            request,
            status_code=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            code=exc.code,
            extra=exc.extra,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return _problem(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Validation failed",
            detail="The request payload is invalid.",
            code="validation_error",
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(
            request,
            status_code=exc.status_code,
            title=str(exc.detail),
            detail=str(exc.detail),
            code=f"http_{exc.status_code}",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return _problem(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal server error",
            detail="An unexpected error occurred.",
            code="internal_error",
        )
