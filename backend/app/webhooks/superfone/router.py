"""Superfone Webhook REST API Router.

Two distinct auth mechanisms, one per webhook stream -- see security.py for
why they differ. Both fail closed: authenticity is checked before any DB
write. Every endpoint accepts a raw JSON body (not a strict Pydantic model)
since Superfone's documented payload shapes are partial/variable -- a
malformed body is caught explicitly and turned into a clean 4xx, never a 500.
"""

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.db.session import get_db_session
from app.webhooks.superfone.repository import SuperfoneCrmTenantConfigRepository
from app.webhooks.superfone.security import (
    verify_sfvopi_webhook_token,
    verify_superfone_crm_bearer,
)
from app.webhooks.superfone.service import SuperfoneWebhookService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/superfone", tags=["Superfone Webhooks"])


async def _read_json_body(request: Request) -> dict[str, Any]:
    """Parse the raw JSON body defensively -- a malformed/non-JSON body from
    a webhook provider is a clean 422, never an unhandled 500."""
    try:
        body = await request.json()
    except Exception as e:
        raise ValidationError(
            message="Webhook body is not valid JSON.",
            code="INVALID_WEBHOOK_BODY",
        ) from e
    if not isinstance(body, dict):
        raise ValidationError(
            message="Webhook body must be a JSON object.",
            code="INVALID_WEBHOOK_BODY",
        )
    return body


# ---------------------------------------------------------------------------
# SFVoPI: answer / ring / hangup
# ---------------------------------------------------------------------------


@router.post(
    "/sfvopi/answer",
    status_code=status.HTTP_200_OK,
    summary="SFVoPI Answer Webhook",
    description=(
        "Superfone calls this when an outbound AI call connects. Must respond "
        "within 10 seconds with Stream JSON pointing at the external voice-agent "
        "WebSocket -- DB bookkeeping happens after the response is built, never "
        "blocking it."
    ),
)
async def sfvopi_answer_webhook(
    request: Request,
    token: Annotated[
        str | None, Query(description="Shared-secret token we embedded in this URL")
    ] = None,
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """SFVoPI answer webhook -- returns Stream JSON."""
    verify_sfvopi_webhook_token(token)
    payload = await _read_json_body(request)

    service = SuperfoneWebhookService(session)
    # The payload carries request_uuid/call_uuid; passing it lets the Stream URL
    # name the call, which is how app/voice/router.py correlates the media
    # socket back to a call job. Still built before any DB work touches it.
    stream_response = service.build_stream_response(payload)

    try:
        await service.handle_sfvopi_answer(payload)
        await session.commit()
    except Exception as e:
        logger.error(f"Error persisting SFVoPI answer webhook (response already built): {e!s}")
        await session.rollback()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=stream_response.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


@router.post(
    "/sfvopi/ring",
    status_code=status.HTTP_200_OK,
    summary="SFVoPI Ring Webhook",
    description=(
        "Superfone calls this when the destination phone starts ringing. Any 2xx is accepted."
    ),
)
async def sfvopi_ring_webhook(
    request: Request,
    token: Annotated[str | None, Query()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """SFVoPI ring webhook -- ack only."""
    verify_sfvopi_webhook_token(token)
    payload = await _read_json_body(request)

    service = SuperfoneWebhookService(session)
    try:
        await service.handle_sfvopi_ring(payload)
        await session.commit()
    except Exception as e:
        logger.error(f"Error processing SFVoPI ring webhook: {e!s}")
        await session.rollback()

    return {"status": "ok"}


@router.post(
    "/sfvopi/hangup",
    status_code=status.HTTP_200_OK,
    summary="SFVoPI Hangup Webhook",
    description="Superfone calls this when the call ends. Any 2xx is accepted.",
)
async def sfvopi_hangup_webhook(
    request: Request,
    token: Annotated[str | None, Query()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """SFVoPI hangup webhook -- ack only."""
    verify_sfvopi_webhook_token(token)
    payload = await _read_json_body(request)

    service = SuperfoneWebhookService(session)
    try:
        await service.handle_sfvopi_hangup(payload)
        await session.commit()
    except Exception as e:
        logger.error(f"Error processing SFVoPI hangup webhook: {e!s}")
        await session.rollback()

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# CRM event notifications
# ---------------------------------------------------------------------------


@router.post(
    "/crm/{tenant_id}/events/{event_type}",
    status_code=status.HTTP_200_OK,
    summary="Superfone CRM Event Notification",
    description=(
        "Dashboard-configured CRM event notification (ALL_CALLS/MISSED_CALL/"
        "CDR_RECORDING_AVAILABLE/CDR_SUMMARY_READY). Routes by tenant_id in "
        "the URL, same as the WhatsApp webhook -- each tenant registers this "
        "URL and its own bearer secret in its own Superfone dashboard "
        "automation. event_type is a path segment we choose per automation "
        "when configuring it, since the payload shape varies per event and "
        "isn't guaranteed to self-describe its type. Authorization: Bearer required."
    ),
)
async def superfone_crm_event_webhook(
    tenant_id: UUID,
    event_type: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Superfone CRM event-notification webhook -- ack only."""
    config_repo = SuperfoneCrmTenantConfigRepository(session)
    secret_hash = await config_repo.get_secret_hash(tenant_id)
    verify_superfone_crm_bearer(request.headers.get("authorization"), secret_hash)
    payload = await _read_json_body(request)

    service = SuperfoneWebhookService(session)
    try:
        await service.handle_crm_event(tenant_id, event_type, payload)
        await session.commit()
    except Exception as e:
        logger.error(
            f"Error processing Superfone CRM event '{event_type}' for tenant {tenant_id}: {e!s}"
        )
        await session.rollback()

    return {"status": "ok"}
