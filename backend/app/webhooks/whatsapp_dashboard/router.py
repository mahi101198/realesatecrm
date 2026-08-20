"""HTTP endpoint the whatsapp_busness_dashboard product's stub
triggerCallAgent posts to. See app/webhooks/whatsapp_dashboard/service.py
for the orchestration this delegates to."""

from typing import Any

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.webhooks.whatsapp_dashboard.schemas import CallAgentTriggerRequest
from app.webhooks.whatsapp_dashboard.security import verify_call_agent_bearer
from app.webhooks.whatsapp_dashboard.service import CallAgentTriggerService

router = APIRouter(prefix="/webhooks/whatsapp-dashboard", tags=["WhatsApp Dashboard Integration"])


@router.post(
    "/call-agent",
    status_code=status.HTTP_200_OK,
    summary="WhatsApp Dashboard Call-Agent Trigger",
    description=(
        "Called by the whatsapp_busness_dashboard product's WhatsApp AI "
        "agent when a customer asks for a human, or on first contact. "
        "Resolves the phone number to a tenant/customer/lead and either "
        "places an immediate outbound call or queues one."
    ),
)
async def trigger_call_agent(
    data: CallAgentTriggerRequest,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Call-agent trigger endpoint."""
    verify_call_agent_bearer(authorization)
    service = CallAgentTriggerService(session)
    return await service.trigger(data)
