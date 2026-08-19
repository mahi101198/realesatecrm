"""Global Exception Handlers for FastAPI."""

import logging
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.request_context import get_request_id

logger = logging.getLogger(__name__)


def build_error_response(
    code: str,
    message: str,
    status_code: int,
    request_id: str,
    details: dict[str, Any] | list[Any] | None = None,
) -> JSONResponse:
    """Build standardized JSON error response body."""
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if details:
        payload["error"]["details"] = details

    return JSONResponse(
        status_code=status_code,
        content=payload,
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle custom application AppError exceptions."""
    request_id = get_request_id() or "unknown"
    logger.warning(
        "Application error occurred",
        extra={
            "error_code": exc.code,
            "status_code": exc.status_code,
            "error_message": exc.message,
            "request_id": request_id,
            "path": request.url.path,
        },
    )
    return build_error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        request_id=request_id,
        details=exc.details if exc.details else None,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic request body/query validation errors."""
    request_id = get_request_id() or "unknown"
    logger.warning(
        "Validation error occurred",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "errors": exc.errors(),
        },
    )
    # Reformat Pydantic validation errors for API consumers safely
    cleaned_errors = [
        {
            "field": " -> ".join(str(loc) for loc in err.get("loc", [])),
            "message": err.get("msg", "Invalid input"),
            "type": err.get("type", "value_error"),
        }
        for err in exc.errors()
    ]

    return build_error_response(
        code="VALIDATION_ERROR",
        message="Request payload or parameters validation failed.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        request_id=request_id,
        details=cleaned_errors,
    )


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle standard HTTP exceptions from FastAPI / Starlette."""
    request_id = get_request_id() or "unknown"
    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        429: "TOO_MANY_REQUESTS",
    }
    code = code_map.get(exc.status_code, "HTTP_ERROR")
    message = str(exc.detail) if exc.detail else "An HTTP error occurred."

    return build_error_response(
        code=code,
        message=message,
        status_code=exc.status_code,
        request_id=request_id,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unhandled exceptions to prevent stack trace leakage in production."""
    request_id = get_request_id() or "unknown"
    logger.error(
        "Unhandled exception occurred during request processing",
        exc_info=exc,
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
        },
    )

    message = (
        str(exc)
        if settings.DEBUG
        else "An unexpected internal server error occurred. Please contact support with request ID."
    )

    return build_error_response(
        code="INTERNAL_SERVER_ERROR",
        message=message,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        request_id=request_id,
    )
