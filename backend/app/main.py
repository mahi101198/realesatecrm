"""FastAPI Application Entrypoint & Infrastructure Configuration."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent.router import router as agent_router
from app.appointments.router import router as appointments_router
from app.auth.router import router as auth_router
from app.bookings.router import router as bookings_router
from app.core.config import settings
from app.core.constants import API_V1_PREFIX
from app.core.error_handlers import (
    app_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.core.exceptions import AppError
from app.core.logging import setup_logging
from app.core.middleware import (
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    StructuredLoggingMiddleware,
    TimingMiddleware,
)
from app.core.request_context import get_request_id
from app.core.security import init_supabase_jwks
from app.customers.router import router as customers_router
from app.db import session as db_session
from app.db.redis import close_redis, get_redis_client, init_redis
from app.db.session import close_db_engine, init_db_engine
from app.followups.router import router as followups_router
from app.leads.router import router as leads_router
from app.locations.router import router as locations_router
from app.ownerships.router import router as ownerships_router
from app.projects.router import router as projects_router
from app.properties.router import router as properties_router
from app.public_intake.router import router as public_intake_router
from app.roles.router import router as roles_router
from app.sales.router import router as sales_router
from app.tenants.router import router as tenants_router
from app.users.router import router as users_router
from app.voice.pipeline import register_inference_providers
from app.voice.router import router as voice_router
from app.webhooks.superfone.router import router as superfone_webhooks_router
from app.webhooks.whatsapp.router import router as whatsapp_webhooks_router
from app.webhooks.whatsapp_dashboard.router import router as whatsapp_dashboard_router
from app.whatsapp.router import router as whatsapp_router
from app.workers.call_scheduler import run_call_scheduler_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for application startup and shutdown hooks."""
    # 1. Configure Structured Logging
    setup_logging()
    logger.info(f"Starting {settings.APP_NAME} in environment '{settings.APP_ENV}'...")

    # 2. Initialize Database Engine & Connection Pool
    init_db_engine()

    # 3. Initialize Ephemeral Redis Client
    await init_redis()

    # 4. Fetch & cache Supabase JWKS public keys (needed for ES256 JWT verification).
    await init_supabase_jwks()

    # 5. Register the AI voice agent's speech providers (LiveKit Inference).
    #    Config-driven and fail-open: with the VOICE_* model settings empty this
    #    registers nothing, `VoiceService.preflight()` then reports the voice
    #    layer as unconfigured, and Superfone media streams are accepted and
    #    closed cleanly -- exactly as they are today.
    register_inference_providers()

    # 6. Start Background Call-Job Scheduler (Redis-locked, single dispatcher)
    call_scheduler_task = asyncio.create_task(run_call_scheduler_loop())

    yield

    # Shutdown sequence -- stop the scheduler before tearing down Redis/DB,
    # since the loop depends on both.
    logger.info("Shutting down application resources...")
    call_scheduler_task.cancel()
    with suppress(asyncio.CancelledError):
        await call_scheduler_task
    await close_redis()
    await close_db_engine()
    logger.info("Application shutdown complete.")


# Instantiate FastAPI Application with OpenAPI Metadata
app = FastAPI(
    title=settings.APP_NAME,
    description="Multi-Tenant Real Estate CRM + AI Voice Sales Platform Backend API",
    version="1.0.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
    lifespan=lifespan,
)

# -----------------------------------------------------------------------------
# MIDDLEWARE REGISTRATION (Order: Outer to Inner)
# -----------------------------------------------------------------------------

# CORS Middleware with strict configured origin allowlist
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(StructuredLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TimingMiddleware)
app.add_middleware(RequestIDMiddleware)


# -----------------------------------------------------------------------------
# EXCEPTION HANDLER REGISTRATION
# -----------------------------------------------------------------------------

app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_exception_handler)


# -----------------------------------------------------------------------------
# API ROUTERS
# -----------------------------------------------------------------------------

app.include_router(auth_router, prefix=API_V1_PREFIX)
app.include_router(customers_router, prefix=API_V1_PREFIX)
app.include_router(leads_router, prefix=API_V1_PREFIX)
app.include_router(locations_router, prefix=API_V1_PREFIX)
app.include_router(projects_router, prefix=API_V1_PREFIX)
app.include_router(properties_router, prefix=API_V1_PREFIX)
app.include_router(roles_router, prefix=API_V1_PREFIX)
app.include_router(appointments_router, prefix=API_V1_PREFIX)
app.include_router(followups_router, prefix=API_V1_PREFIX)
app.include_router(bookings_router, prefix=API_V1_PREFIX)
app.include_router(sales_router, prefix=API_V1_PREFIX)
app.include_router(ownerships_router, prefix=API_V1_PREFIX)
app.include_router(public_intake_router, prefix=API_V1_PREFIX)
app.include_router(superfone_webhooks_router, prefix=API_V1_PREFIX)
app.include_router(whatsapp_webhooks_router, prefix=API_V1_PREFIX)
app.include_router(whatsapp_dashboard_router, prefix=API_V1_PREFIX)
app.include_router(whatsapp_router, prefix=API_V1_PREFIX)
app.include_router(users_router, prefix=API_V1_PREFIX)
app.include_router(tenants_router, prefix=API_V1_PREFIX)
app.include_router(agent_router, prefix=API_V1_PREFIX)
# The Superfone media-stream WebSocket. Additive: with LiveKit unconfigured
# it accepts and immediately closes, so mounting it changes nothing about
# the existing call flow.
app.include_router(voice_router, prefix=API_V1_PREFIX)

# -----------------------------------------------------------------------------
# INFRASTRUCTURE / HEALTH ENDPOINTS
# -----------------------------------------------------------------------------


@app.get(
    f"{API_V1_PREFIX}/health",
    status_code=status.HTTP_200_OK,
    tags=["Infrastructure"],
    summary="Liveness Check",
    description="Verifies if the application web process is alive and accepting requests.",
)
async def health_check() -> dict[str, Any]:
    """Liveness probe: Returns healthy if application process is running."""
    return {
        "status": "healthy",
        "environment": settings.APP_ENV,
        "request_id": get_request_id(),
    }


@app.get(
    f"{API_V1_PREFIX}/ready",
    tags=["Infrastructure"],
    summary="Readiness Check",
    description="Verifies readiness of required external dependencies (PostgreSQL and Redis).",
)
async def readiness_check() -> JSONResponse:
    """Readiness probe: Validates PostgreSQL database and Redis connectivity."""
    db_status = "unavailable"
    redis_status = "unavailable"
    is_ready = True

    # 1. Verify PostgreSQL Database Readiness
    if db_session.engine is not None:
        try:
            async with db_session.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            db_status = "ready"
        except Exception as e:
            logger.error(f"Readiness DB check failed: {e!s}")
            is_ready = False
    else:
        is_ready = False

    # 2. Verify Redis Readiness
    redis_client = get_redis_client()
    if redis_client is not None:
        try:
            await redis_client.ping()
            redis_status = "ready"
        except Exception as e:
            logger.error(f"Readiness Redis check failed: {e!s}")
            is_ready = False
    else:
        is_ready = False

    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if is_ready else "unavailable",
            "dependencies": {
                "database": db_status,
                "redis": redis_status,
            },
            "request_id": get_request_id(),
        },
    )
