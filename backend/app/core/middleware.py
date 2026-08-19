"""Core Starlette/FastAPI Security & Observability Middleware Stack."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.constants import REQUEST_ID_HEADER, RESPONSE_TIME_HEADER
from app.core.request_context import set_request_id

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generates or propagates unique request correlation ID (X-Request-ID)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Accept client header if valid non-empty string, else generate UUID4
        incoming_id = request.headers.get(REQUEST_ID_HEADER)
        if incoming_id and len(incoming_id.strip()) <= 128:
            request_id = incoming_id.strip()
        else:
            request_id = str(uuid.uuid4())

        set_request_id(request_id)
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class TimingMiddleware(BaseHTTPMiddleware):
    """Measures request execution duration and attaches X-Response-Time header."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        response.headers[RESPONSE_TIME_HEADER] = f"{duration_ms:.2f}ms"
        request.state.duration_ms = duration_ms
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attaches standard security headers suitable for API services."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Logs incoming HTTP request processing summary."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        duration_ms = getattr(request.state, "duration_ms", 0.0)
        request_id = getattr(request.state, "request_id", "unknown")

        logger.info(
            f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.2f}ms)",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "client_ip": request.client.host if request.client else None,
            },
        )
        return response
