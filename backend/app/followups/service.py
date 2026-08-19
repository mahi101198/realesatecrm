"""Follow-up Service Layer."""

import logging
from math import ceil
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.followups.repository import FollowUpRepository
from app.followups.schemas import (
    FollowUpCompleteRequest,
    FollowUpCreate,
    FollowUpFilter,
    FollowUpResponse,
    FollowUpUpdate,
)
from app.leads.repository import LeadRepository
from app.shared.schemas import PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)


class FollowUpService:
    """Service handling business rules and workflows for Follow-ups."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = FollowUpRepository(session)
        self.lead_repository = LeadRepository(session)

    async def create_followup(
        self, tenant_id: UUID, created_by: UUID | None, data: FollowUpCreate
    ) -> FollowUpResponse:
        """Create a follow-up task for a lead."""
        lead = await self.lead_repository.get_by_id(tenant_id, data.lead_id)
        if not lead:
            raise NotFoundError(
                message=f"Lead with ID '{data.lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )

        row = await self.repository.create(
            tenant_id=tenant_id,
            customer_id=lead["customer_id"],
            created_by=created_by,
            data=data,
        )
        return FollowUpResponse.model_validate(row)

    async def get_followup(self, tenant_id: UUID | None, followup_id: UUID) -> FollowUpResponse:
        """Fetch follow-up by ID."""
        row = await self.repository.get_by_id(tenant_id, followup_id)
        if not row:
            raise NotFoundError(
                message=f"Follow-up with ID '{followup_id}' was not found.",
                code="FOLLOWUP_NOT_FOUND",
            )
        return FollowUpResponse.model_validate(row)

    async def update_followup(
        self, tenant_id: UUID | None, followup_id: UUID, data: FollowUpUpdate
    ) -> FollowUpResponse:
        """Update follow-up task details."""
        existing = await self.repository.get_by_id(tenant_id, followup_id)
        if not existing:
            raise NotFoundError(
                message=f"Follow-up with ID '{followup_id}' was not found.",
                code="FOLLOWUP_NOT_FOUND",
            )

        updated_row = await self.repository.update(tenant_id, followup_id, data)
        if not updated_row:
            raise NotFoundError(
                message=f"Follow-up with ID '{followup_id}' was not found.",
                code="FOLLOWUP_NOT_FOUND",
            )
        return FollowUpResponse.model_validate(updated_row)

    async def complete_followup(
        self,
        tenant_id: UUID | None,
        followup_id: UUID,
        completed_by: UUID | None,
        data: FollowUpCompleteRequest,
    ) -> FollowUpResponse:
        """Mark follow-up task completed."""
        existing = await self.repository.get_by_id(tenant_id, followup_id)
        if not existing:
            raise NotFoundError(
                message=f"Follow-up with ID '{followup_id}' was not found.",
                code="FOLLOWUP_NOT_FOUND",
            )

        completed_row = await self.repository.complete(
            tenant_id, followup_id, completed_by, data.completion_notes
        )
        if not completed_row:
            raise NotFoundError(
                message=f"Follow-up with ID '{followup_id}' was not found.",
                code="FOLLOWUP_NOT_FOUND",
            )
        return FollowUpResponse.model_validate(completed_row)

    async def list_followups(
        self,
        tenant_id: UUID | None,
        filters: FollowUpFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[FollowUpResponse]:
        """Search and list follow-ups."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [FollowUpResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[FollowUpResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )
