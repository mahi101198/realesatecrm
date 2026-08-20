"""Tenant-scoped Meta WhatsApp webhook receiver.

Routes by tenant_id in the URL path -- never by parsing the (still
unverified) payload first. Each tenant registers their own Meta App's
webhook subscription pointing at
`{APP_PUBLIC_BASE_URL}/api/v1/webhooks/whatsapp/{their_tenant_id}`.
"""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.integrations.whatsapp.repository import WhatsAppTenantConfigRepository
from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
    parse_webhook_events,
)
from app.webhooks.whatsapp.security import verify_meta_signature, verify_meta_verify_token
from app.webhooks.whatsapp.service import WhatsAppWebhookService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp Webhooks"])


@router.get("/{tenant_id}")
async def whatsapp_webhook_handshake(
    tenant_id: UUID,
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Meta's webhook subscription verification handshake."""
    repo = WhatsAppTenantConfigRepository(session)
    config = await repo.get_decrypted(tenant_id)
    if not config or not config["is_active"]:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    if hub_mode == "subscribe" and verify_meta_verify_token(
        hub_verify_token, config["verify_token"]
    ):
        return Response(content=hub_challenge, status_code=status.HTTP_200_OK)
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/{tenant_id}")
async def whatsapp_webhook_receive(
    tenant_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Receive and process a Meta WhatsApp webhook delivery. Always returns
    200 once signature verification passes, even if event processing
    raises -- a validly-signed request that fails internally will fail
    identically on retry, while sustained webhook failures cause Meta to
    disable the subscription outright."""
    repo = WhatsAppTenantConfigRepository(session)
    config = await repo.get_decrypted(tenant_id)
    if not config or not config["is_active"]:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_meta_signature(raw_body, signature, config["app_secret"]):
        logger.warning(f"whatsapp webhook: invalid signature for tenant {tenant_id}")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        payload = json.loads(raw_body)
        events = parse_webhook_events(payload)
        service = WhatsAppWebhookService(session)
        for event in events:
            if isinstance(event, InboundMessageEvent):
                await service.handle_inbound_message(tenant_id, event)
            elif isinstance(event, StatusUpdateEvent):
                await service.handle_status_update(event)
            elif isinstance(event, TemplateStatusUpdateEvent):
                await service.handle_template_status_update(event)
        await session.commit()
    except Exception:
        logger.exception(f"whatsapp webhook: failed to process payload for tenant {tenant_id}")
        await session.rollback()

    return Response(content="EVENT_RECEIVED", status_code=status.HTTP_200_OK)
