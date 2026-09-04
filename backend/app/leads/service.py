"""Lead Business Service Layer."""

import logging
from math import ceil
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.customers.repository import CustomerRepository
from app.leads.repository import LeadRepository
from app.leads.schemas import (
    LeadAssign,
    LeadCreate,
    LeadFilter,
    LeadNoteCreate,
    LeadNoteResponse,
    LeadPropertyInterestCreate,
    LeadPropertyInterestResponse,
    LeadResponse,
    LeadUpdate,
    SalesAgentResponse,
)
from app.shared.schemas import PaginatedResponse, PaginationParams
from app.shared.utils import utcnow

logger = logging.getLogger(__name__)


class LeadService:
    """Service handling business logic and workflows for Leads."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = LeadRepository(session)
        self.customer_repository = CustomerRepository(session)

    async def create_lead(
        self, tenant_id: UUID, created_by: UUID | None, data: LeadCreate
    ) -> LeadResponse:
        """Create a lead for an existing customer in tenant."""
        customer = await self.customer_repository.get_by_id(tenant_id, data.customer_id)
        if not customer:
            raise NotFoundError(
                message=f"Customer with ID '{data.customer_id}' was not found.",
                code="CUSTOMER_NOT_FOUND",
            )

        if data.assigned_sales_agent_id:
            await self._validate_sales_agent(tenant_id, data.assigned_sales_agent_id)

        row = await self.repository.create(tenant_id, created_by, data)
        return LeadResponse.model_validate(row)

    async def get_lead(self, tenant_id: UUID | None, lead_id: UUID) -> LeadResponse:
        """Fetch lead by ID."""
        row = await self.repository.get_by_id(tenant_id, lead_id)
        if not row:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )
        return LeadResponse.model_validate(row)

    async def update_lead(
        self, tenant_id: UUID | None, lead_id: UUID, data: LeadUpdate
    ) -> LeadResponse:
        """Update lead details."""
        existing = await self.repository.get_by_id(tenant_id, lead_id)
        if not existing:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )

        effective_tenant = tenant_id or existing["tenant_id"]

        if data.assigned_sales_agent_id and data.assigned_sales_agent_id != existing.get(
            "assigned_sales_agent_id"
        ):
            await self._validate_sales_agent(effective_tenant, data.assigned_sales_agent_id)

        # Handle status transitions timestamps
        if data.status and data.status != existing["status"]:
            if data.status == "converted" and not existing.get("converted_at"):
                data_dict = data.model_dump(exclude_unset=True)
                data_dict["converted_at"] = utcnow()
                data = LeadUpdate(**data_dict)
            elif data.status == "lost" and not existing.get("lost_at"):
                data_dict = data.model_dump(exclude_unset=True)
                data_dict["lost_at"] = utcnow()
                data = LeadUpdate(**data_dict)

        updated_row = await self.repository.update(tenant_id, lead_id, data)
        if not updated_row:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )
        return LeadResponse.model_validate(updated_row)

    async def list_leads(
        self,
        tenant_id: UUID | None,
        filters: LeadFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[LeadResponse]:
        """Search and list leads with pagination."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [LeadResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[LeadResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )

    async def assign_lead(
        self,
        tenant_id: UUID,
        lead_id: UUID,
        assignment: LeadAssign,
        assigned_by: UUID | None,
    ) -> LeadResponse:
        """Assign sales agent to lead within tenant."""
        lead = await self.repository.get_by_id(tenant_id, lead_id)
        if not lead:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )

        await self._validate_sales_agent(tenant_id, assignment.sales_agent_id)

        updated_row = await self.repository.assign_lead(
            tenant_id=tenant_id,
            lead_id=lead_id,
            sales_agent_id=assignment.sales_agent_id,
            assigned_by=assigned_by,
            reason=assignment.reason,
        )
        if not updated_row:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )
        return LeadResponse.model_validate(updated_row)

    async def add_property_interest(
        self, tenant_id: UUID, lead_id: UUID, data: LeadPropertyInterestCreate
    ) -> LeadPropertyInterestResponse:
        """Add project/property interest to a lead."""
        lead = await self.repository.get_by_id(tenant_id, lead_id)
        if not lead:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )

        # Verify project belongs to tenant
        proj_query = text(
            "SELECT id FROM public.projects "
            "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
        )
        proj_check = await self.session.execute(
            proj_query, {"id": data.project_id, "tenant_id": tenant_id}
        )
        if not proj_check.scalar_one_or_none():
            raise NotFoundError(
                message=f"Project with ID '{data.project_id}' was not found in this tenant.",
                code="PROJECT_NOT_FOUND",
            )

        if data.property_id:
            prop_query = text(
                "SELECT id FROM public.properties "
                "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
            )
            prop_check = await self.session.execute(
                prop_query, {"id": data.property_id, "tenant_id": tenant_id}
            )
            if not prop_check.scalar_one_or_none():
                raise NotFoundError(
                    message=f"Property with ID '{data.property_id}' was not found in this tenant.",
                    code="PROPERTY_NOT_FOUND",
                )

        row = await self.repository.add_property_interest(tenant_id, lead_id, data)
        return LeadPropertyInterestResponse.model_validate(row)

    async def remove_property_interest(
        self, tenant_id: UUID, lead_id: UUID, interest_id: UUID
    ) -> None:
        """Remove a property interest entry."""
        success = await self.repository.remove_property_interest(tenant_id, lead_id, interest_id)
        if not success:
            raise NotFoundError(
                message=f"Property interest '{interest_id}' not found for lead '{lead_id}'.",
                code="NOT_FOUND",
            )

    async def list_property_interests(
        self, tenant_id: UUID, lead_id: UUID
    ) -> list[LeadPropertyInterestResponse]:
        """List property interests for a lead."""
        lead = await self.repository.get_by_id(tenant_id, lead_id)
        if not lead:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )
        rows = await self.repository.list_property_interests(tenant_id, lead_id)
        return [LeadPropertyInterestResponse.model_validate(r) for r in rows]

    async def add_note(
        self, tenant_id: UUID, lead_id: UUID, author_id: UUID, data: LeadNoteCreate
    ) -> LeadNoteResponse:
        """Add note to a lead."""
        lead = await self.repository.get_by_id(tenant_id, lead_id)
        if not lead:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )

        row = await self.repository.add_note(
            tenant_id=tenant_id,
            lead_id=lead_id,
            customer_id=lead["customer_id"],
            author_id=author_id,
            note_text=data.note_text,
            is_pinned=data.is_pinned,
        )
        return LeadNoteResponse.model_validate(row)

    async def list_notes(self, tenant_id: UUID, lead_id: UUID) -> list[LeadNoteResponse]:
        """List notes for a lead."""
        lead = await self.repository.get_by_id(tenant_id, lead_id)
        if not lead:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )
        rows = await self.repository.list_notes(tenant_id, lead_id)
        return [LeadNoteResponse.model_validate(r) for r in rows]

    async def list_sales_agents(self, tenant_id: UUID) -> list[SalesAgentResponse]:
        """List active sales agents assignable to leads in this tenant --
        the picker a lead-assignment UI needs, since assigned_sales_agent_id
        is a sales_agents.id, not a staff user's id."""
        rows = await self.repository.list_sales_agents(tenant_id)
        return [SalesAgentResponse.model_validate(r) for r in rows]

    async def _validate_sales_agent(self, tenant_id: UUID, sales_agent_id: UUID) -> None:
        """Validate that sales agent exists, is active, and belongs to tenant."""
        query = text(
            """
            SELECT id FROM public.sales_agents
            WHERE id = :agent_id
              AND tenant_id = :tenant_id
              AND is_active = true
              AND deleted_at IS NULL
            """
        )
        result = await self.session.execute(
            query, {"agent_id": sales_agent_id, "tenant_id": tenant_id}
        )
        if not result.scalar_one_or_none():
            raise NotFoundError(
                message=f"Sales agent '{sales_agent_id}' was not found in this tenant.",
                code="SALES_AGENT_NOT_FOUND",
            )
