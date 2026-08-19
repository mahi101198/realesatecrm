"""Sales Handoff Acceptance Service.

The natural integration point for click-to-call is NOT the moment a handoff
is requested (request_sales_agent_transfer_tool / create_sales_handoff) --
at that point no specific staff member or phone number is known yet, only
that a human transfer is wanted. Superfone's click-to-call requires a real
`user_number` (a specific staff member's Superfone-registered phone) and
`customer_number`, so it can only fire once a staff member actually accepts
the handoff. This service is that acceptance action.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.repository import AgentRepository
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.integrations.superfone.factory import get_superfone_crm_client

logger = logging.getLogger(__name__)


class SalesHandoffService:
    """Service accepting a sales handoff and bridging the call via Superfone
    CRM click-to-call."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = AgentRepository(session)

    async def accept_handoff(
        self, tenant_id: UUID, handoff_id: UUID, assigned_user_id: UUID
    ) -> dict[str, Any]:
        """Accept a handoff: validate it's still open, resolve both phone
        numbers, place the click-to-call bridge, THEN mark it accepted.

        Fail closed: the handoff is only marked accepted if the click-to-call
        request actually succeeds -- a failed bridge must not leave the
        system believing a human was connected.
        """
        handoff = await self.repository.get_sales_handoff(tenant_id, handoff_id)
        if not handoff:
            raise NotFoundError(
                message=f"Sales handoff '{handoff_id}' was not found.",
                code="SALES_HANDOFF_NOT_FOUND",
            )
        if handoff["status"] not in ("requested", "queued"):
            raise ConflictError(
                message=f"Sales handoff '{handoff_id}' is already '{handoff['status']}'.",
                code="SALES_HANDOFF_NOT_OPEN",
            )

        user_phone = await self.repository.get_user_phone(tenant_id, assigned_user_id)
        if not user_phone:
            raise ValidationError(
                message=(
                    "The assigned staff member has no registered phone number on file, "
                    "so a click-to-call bridge cannot be placed."
                ),
                code="ASSIGNED_USER_NO_PHONE",
            )

        customer_phone = handoff["customer_phone"]
        if not customer_phone:
            raise ValidationError(
                message="The customer for this handoff has no phone number on file.",
                code="CUSTOMER_NO_PHONE",
            )

        client = get_superfone_crm_client()
        # Errors (incl. 404 SUPERFONE_USER_NOT_REGISTERED if this staff
        # member's phone isn't set up for Superfone Dashboard Calling)
        # propagate as-is -- clean, typed, already mapped by the client.
        click_result = await client.click_to_call(
            customer_number=customer_phone, user_number=user_phone, call_action="START"
        )

        updated = await self.repository.accept_sales_handoff(
            tenant_id, handoff_id, assigned_user_id
        )
        if not updated:
            # Lost a race against another acceptance between our check above
            # and this UPDATE -- the click-to-call bridge we just placed is
            # already live, but the handoff record itself is not ours to claim.
            raise ConflictError(
                message=f"Sales handoff '{handoff_id}' was accepted by someone else first.",
                code="SALES_HANDOFF_ACCEPT_RACE",
            )

        updated["click_to_call_request_uuid"] = click_result.get("request_uuid")
        return updated
