"""Orchestrates the whatsapp-dashboard call-agent trigger: resolve the
caller's phone number to a tenant/contact/lead, then either place an immediate
outbound call (reason='requested') or queue a call_job for the background
scheduler (any other reason).

Neither branch contains call logic of its own any more: both go through
`app.agent.tools.call_tools`, so this trigger, the AI tool registry and the
background worker all place calls through exactly one function.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.call_tools import make_instant_call, schedule_call
from app.leads.resolver import LeadResolver
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
        self.lead_resolver = LeadResolver(session)

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

        # The tenant comes from the matched customer row, never from the
        # request body -- see find_customer_by_phone_cross_tenant's docstring
        # for why an ambiguous phone number is refused rather than guessed.
        tenant_id = customer["tenant_id"]
        contact_id = customer["id"]

        lead = await self.lead_resolver.resolve_lead(
            tenant_id, contact_id, context={"source": "whatsapp_dashboard"}
        )
        lead_id = lead["id"]

        call_context = {
            "job_type": _JOB_TYPE,
            "reason": data.reason,
            "source": "whatsapp_dashboard",
        }

        if data.reason != "requested":
            job = await schedule_call(
                tenant_id,
                contact_id,
                lead_id,
                customer.get("phone"),
                None,
                {**call_context, "priority": _QUEUED_PRIORITY},
                session=self.session,
            )
            return {"success": True, "data": {"call_job_id": str(job["id"]), "queued": True}}

        result = await make_instant_call(
            tenant_id,
            contact_id,
            lead_id,
            customer.get("phone"),
            {**call_context, "priority": _IMMEDIATE_PRIORITY},
            session=self.session,
        )
        return {"success": bool(result.get("success", True)), "data": result}
