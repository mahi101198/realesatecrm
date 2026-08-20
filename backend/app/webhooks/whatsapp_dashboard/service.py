"""Orchestrates the whatsapp-dashboard call-agent trigger: resolve the
caller's phone number to a tenant/customer/lead, then either place an
immediate outbound call (reason='requested') or queue a normal-priority
call_job for whatever future dispatcher drives it (any other reason)."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.gateway import AgentGateway
from app.agent.orchestrator import CallOrchestrator
from app.webhooks.whatsapp_dashboard.repository import CallAgentTriggerRepository
from app.webhooks.whatsapp_dashboard.schemas import CallAgentTriggerRequest

logger = logging.getLogger(__name__)

_IMMEDIATE_PRIORITY = 1
_QUEUED_PRIORITY = 5
_JOB_TYPE = "whatsapp_callback_request"


class CallAgentTriggerService:
    """Handles POST /webhooks/whatsapp-dashboard/call-agent."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = CallAgentTriggerRepository(session)

    async def trigger(self, data: CallAgentTriggerRequest) -> dict[str, Any]:
        """Best-effort by design: the caller's own fetch is wrapped in
        try/catch and ignores the response, so failures here are logged
        and returned as a clean payload, never raised."""
        customer = await self.repository.find_customer_by_phone_cross_tenant(data.phone)
        if not customer:
            logger.info(
                f"call-agent trigger: no unambiguous customer match for phone "
                f"(reason={data.reason!r})"
            )
            return {"success": False, "error_code": "CUSTOMER_NOT_FOUND"}

        tenant_id = customer["tenant_id"]
        customer_id = customer["id"]
        lead = await self.repository.get_or_create_lead(tenant_id, customer_id)
        lead_id = lead["id"]

        orchestrator = CallOrchestrator(self.session)
        priority = _IMMEDIATE_PRIORITY if data.reason == "requested" else _QUEUED_PRIORITY
        job = await orchestrator.create_call_job(
            tenant_id=tenant_id,
            lead_id=lead_id,
            customer_id=customer_id,
            job_type=_JOB_TYPE,
            priority=priority,
        )

        if data.reason != "requested":
            return {"success": True, "data": {"call_job_id": str(job["id"]), "queued": True}}

        gateway = AgentGateway(self.session)
        await gateway.prepare_call(tenant_id, job["id"])
        result = await gateway.start_call(tenant_id, job["id"])
        return {"success": bool(result.get("success", True)), "data": result}
