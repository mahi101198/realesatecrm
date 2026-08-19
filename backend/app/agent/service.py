"""Agent Service Layer for Server-Derived AI Context."""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import (
    AgentAssignedAgentSummary,
    AgentCustomerSummary,
    AgentLeadContextResponse,
    AgentPropertyInterestSummary,
)
from app.core.exceptions import NotFoundError

logger = logging.getLogger(__name__)


class AgentContextService:
    """Service to derive compact, low-latency voice agent lead context."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_lead_context(
        self, tenant_id: UUID | None, lead_id: UUID
    ) -> AgentLeadContextResponse:
        """Derive complete compact lead context payload for AI voice agent."""
        # 1. Fetch Lead + Customer details in a single query
        lead_query = text(
            """
            SELECT
                l.id AS lead_id,
                l.tenant_id,
                l.lead_number,
                l.status,
                l.sales_stage,
                l.lead_score,
                l.budget_min,
                l.budget_max,
                l.preferred_city,
                l.preferred_locality,
                pt.name AS property_type_name,
                c.id AS customer_id,
                c.full_name AS customer_full_name,
                c.phone AS customer_phone,
                c.email AS customer_email,
                c.city AS customer_city,
                c.preferred_language AS customer_lang,
                sa.id AS sales_agent_id,
                u.name AS sales_agent_name,
                u.phone AS sales_agent_phone
            FROM public.leads l
            JOIN public.customers c ON l.customer_id = c.id
            LEFT JOIN public.property_types pt ON l.property_type_id = pt.id
            LEFT JOIN public.sales_agents sa ON l.assigned_sales_agent_id = sa.id
            LEFT JOIN public.users u ON sa.user_id = u.id
            WHERE l.id = :lead_id
              AND l.deleted_at IS NULL
              AND (:tenant_id::uuid IS NULL OR l.tenant_id = :tenant_id)
            """
        )
        result = await self.session.execute(
            lead_query, {"lead_id": lead_id, "tenant_id": tenant_id}
        )
        lead_row = result.mappings().one_or_none()

        if not lead_row:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )

        # 2. Fetch property interests
        interests_query = text(
            """
            SELECT
                lpi.project_id,
                p.name AS project_name,
                lpi.property_id,
                lpi.interest_level,
                lpi.is_primary
            FROM public.lead_property_interests lpi
            JOIN public.projects p ON lpi.project_id = p.id
            WHERE lpi.lead_id = :lead_id
            ORDER BY lpi.is_primary DESC, lpi.created_at DESC
            LIMIT 5
            """
        )
        interests_res = await self.session.execute(interests_query, {"lead_id": lead_id})
        interests_rows = interests_res.mappings().all()

        # 3. Fetch recent notes
        notes_query = text(
            """
            SELECT note_text FROM public.lead_notes
            WHERE lead_id = :lead_id
            ORDER BY is_pinned DESC, created_at DESC
            LIMIT 5
            """
        )
        notes_res = await self.session.execute(notes_query, {"lead_id": lead_id})
        recent_notes = [r["note_text"] for r in notes_res.mappings().all()]

        customer_summary = AgentCustomerSummary(
            customer_id=lead_row["customer_id"],
            full_name=lead_row["customer_full_name"],
            phone=lead_row["customer_phone"],
            email=lead_row["customer_email"],
            city=lead_row["customer_city"],
            preferred_language=lead_row["customer_lang"],
        )

        property_interests = [
            AgentPropertyInterestSummary(
                project_id=r["project_id"],
                project_name=r["project_name"],
                property_id=r["property_id"],
                interest_level=r["interest_level"],
                is_primary=r["is_primary"],
            )
            for r in interests_rows
        ]

        assigned_agent = None
        if lead_row["sales_agent_id"]:
            assigned_agent = AgentAssignedAgentSummary(
                sales_agent_id=lead_row["sales_agent_id"],
                name=lead_row["sales_agent_name"],
                phone=lead_row["sales_agent_phone"],
            )

        return AgentLeadContextResponse(
            lead_id=lead_row["lead_id"],
            tenant_id=lead_row["tenant_id"],
            lead_number=lead_row["lead_number"],
            status=lead_row["status"],
            sales_stage=lead_row["sales_stage"],
            lead_score=lead_row["lead_score"],
            budget_min=lead_row["budget_min"],
            budget_max=lead_row["budget_max"],
            preferred_city=lead_row["preferred_city"],
            preferred_locality=lead_row["preferred_locality"],
            property_type=lead_row["property_type_name"],
            customer=customer_summary,
            property_interests=property_interests,
            recent_notes=recent_notes,
            assigned_sales_agent=assigned_agent,
        )
