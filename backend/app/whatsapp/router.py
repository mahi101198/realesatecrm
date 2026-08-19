"""WhatsApp Messaging REST API Router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.shared.schemas import PaginatedResponse, PaginationParams
from app.whatsapp.schemas import (
    WhatsAppMessageFilter,
    WhatsAppMessageResponse,
    WhatsAppSendRequest,
    WhatsAppTemplateResponse,
)
from app.whatsapp.service import WhatsAppService

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Messaging"])


@router.post(
    "/messages",
    response_model=WhatsAppMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send WhatsApp Message",
    description=(
        "Send a WhatsApp message to a customer. Template messages work anytime; "
        "any other message type is rejected with a clean error if the customer "
        "hasn't messaged within the last 24 hours (WhatsApp session-window rule)."
    ),
)
async def send_message(
    data: WhatsAppSendRequest,
    context: RequestContext = Depends(require_permission(Permission.WHATSAPP_MESSAGE_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> WhatsAppMessageResponse:
    """Send WhatsApp message endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to send a WhatsApp message.")
    service = WhatsAppService(session)
    return await service.send_message(tenant_id, context.user_id, data)


@router.get(
    "/messages",
    response_model=PaginatedResponse[WhatsAppMessageResponse],
    status_code=status.HTTP_200_OK,
    summary="List WhatsApp Messages",
    description="List and filter WhatsApp message history by customer or lead.",
)
async def list_messages(
    customer_id: Annotated[UUID | None, Query(description="Filter by customer ID")] = None,
    lead_id: Annotated[UUID | None, Query(description="Filter by lead ID")] = None,
    direction: Annotated[str | None, Query(description="inbound/outbound")] = None,
    message_status: Annotated[
        str | None, Query(alias="status", description="queued/sent/delivered/read/failed")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.WHATSAPP_MESSAGE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[WhatsAppMessageResponse]:
    """List WhatsApp messages endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    filters = WhatsAppMessageFilter(
        customer_id=customer_id,
        lead_id=lead_id,
        direction=direction,
        status=message_status,
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = WhatsAppService(session)
    return await service.list_messages(tenant_id, filters, pagination)


@router.get(
    "/templates",
    response_model=list[WhatsAppTemplateResponse],
    status_code=status.HTTP_200_OK,
    summary="List WhatsApp Templates",
    description=(
        "List approved/pending/rejected WhatsApp templates -- a live passthrough "
        "to Superfone/Meta. Pass refresh=true to bypass Superfone's cache."
    ),
)
async def list_templates(
    refresh: Annotated[bool, Query(description="Bypass cache, pull fresh from Meta")] = False,
    _context: RequestContext = Depends(require_permission(Permission.WHATSAPP_TEMPLATE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[WhatsAppTemplateResponse]:
    """List WhatsApp templates endpoint."""
    service = WhatsAppService(session)
    return await service.list_templates(refresh=refresh)
