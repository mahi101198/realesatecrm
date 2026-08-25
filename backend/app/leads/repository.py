"""Lead Repository for PostgreSQL database operations."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.leads.schemas import (
    LeadCreate,
    LeadFilter,
    LeadPropertyInterestCreate,
    LeadUpdate,
)
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class LeadRepository:
    """Repository handling database access for Lead entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, tenant_id: UUID, created_by: UUID | None, data: LeadCreate
    ) -> dict[str, Any]:
        """Insert a new lead record into PostgreSQL."""
        query = text(
            """
            INSERT INTO public.leads (
                tenant_id, customer_id, lead_source_id, campaign_id,
                property_type_id, purpose, preferred_city, preferred_locality,
                budget_min, budget_max, area_min, area_max, area_unit,
                bedrooms, timeline, finance_requirement, status, sales_stage,
                lead_score, interest_level, assigned_sales_agent_id, notes,
                metadata, created_by
            ) VALUES (
                :tenant_id, :customer_id, :lead_source_id, :campaign_id,
                :property_type_id, CAST(:purpose AS public.purpose),
                :preferred_city, :preferred_locality,
                :budget_min, :budget_max, :area_min, :area_max,
                CAST(:area_unit AS public.area_unit),
                :bedrooms, :timeline, CAST(:finance_requirement AS public.finance_requirement),
                CAST(:status AS public.lead_status), CAST(:sales_stage AS public.sales_stage),
                :lead_score, CAST(:interest_level AS public.interest_level),
                :assigned_sales_agent_id, :notes,
                :metadata, :created_by
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "customer_id": data.customer_id,
            "lead_source_id": data.lead_source_id,
            "campaign_id": data.campaign_id,
            "property_type_id": data.property_type_id,
            "purpose": data.purpose,
            "preferred_city": data.preferred_city,
            "preferred_locality": data.preferred_locality,
            "budget_min": data.budget_min,
            "budget_max": data.budget_max,
            "area_min": data.area_min,
            "area_max": data.area_max,
            "area_unit": data.area_unit,
            "bedrooms": data.bedrooms,
            "timeline": data.timeline,
            "finance_requirement": data.finance_requirement,
            "status": data.status,
            "sales_stage": data.sales_stage,
            "lead_score": data.lead_score,
            "interest_level": data.interest_level,
            "assigned_sales_agent_id": data.assigned_sales_agent_id,
            "notes": data.notes,
            "metadata": data.metadata or {},
            "created_by": created_by,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def get_by_id(self, tenant_id: UUID | None, lead_id: UUID) -> dict[str, Any] | None:
        """Fetch lead by ID with optional tenant filtering."""
        if tenant_id is not None:
            query = text(
                """
                SELECT * FROM public.leads
                WHERE id = :lead_id AND tenant_id = :tenant_id AND deleted_at IS NULL
                """
            )
            params = {"lead_id": lead_id, "tenant_id": tenant_id}
        else:
            query = text(
                """
                SELECT * FROM public.leads
                WHERE id = :lead_id AND deleted_at IS NULL
                """
            )
            params = {"lead_id": lead_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update(
        self, tenant_id: UUID | None, lead_id: UUID, data: LeadUpdate
    ) -> dict[str, Any] | None:
        """Update existing lead fields."""
        data_dict = data.model_dump(exclude_unset=True)
        if not data_dict:
            return await self.get_by_id(tenant_id, lead_id)

        set_clauses: list[str] = []
        params: dict[str, Any] = {"lead_id": lead_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        enum_casts = {
            "purpose": "public.purpose",
            "area_unit": "public.area_unit",
            "finance_requirement": "public.finance_requirement",
            "status": "public.lead_status",
            "sales_stage": "public.sales_stage",
            "interest_level": "public.interest_level",
        }

        for key, value in data_dict.items():
            param_key = f"val_{key}"
            if key in enum_casts:
                set_clauses.append(f"{key} = :{param_key}::{enum_casts[key]}")
            else:
                set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :lead_id AND deleted_at IS NULL"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.leads
            SET {set_str}, updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def search(
        self,
        tenant_id: UUID | None,
        filters: LeadFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search and list leads with database-side filtering and pagination."""
        where_conditions = ["deleted_at IS NULL"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.customer_id:
            where_conditions.append("customer_id = :filter_customer_id")
            params["filter_customer_id"] = filters.customer_id

        if filters.status:
            where_conditions.append("status = CAST(:filter_status AS public.lead_status)")
            params["filter_status"] = filters.status

        if filters.sales_stage:
            where_conditions.append("sales_stage = CAST(:filter_sales_stage AS public.sales_stage)")
            params["filter_sales_stage"] = filters.sales_stage

        if filters.assigned_sales_agent_id:
            where_conditions.append("assigned_sales_agent_id = :filter_agent_id")
            params["filter_agent_id"] = filters.assigned_sales_agent_id

        if filters.campaign_id:
            where_conditions.append("campaign_id = :filter_campaign_id")
            params["filter_campaign_id"] = filters.campaign_id

        if filters.property_type_id:
            where_conditions.append("property_type_id = :filter_property_type_id")
            params["filter_property_type_id"] = filters.property_type_id

        if filters.min_budget is not None:
            where_conditions.append("budget_max >= :min_budget")
            params["min_budget"] = filters.min_budget

        if filters.max_budget is not None:
            where_conditions.append("budget_min <= :max_budget")
            params["max_budget"] = filters.max_budget

        if filters.query:
            where_conditions.append(
                "(lead_number ILIKE :search_q OR "
                "preferred_city ILIKE :search_q OR "
                "preferred_locality ILIKE :search_q)"
            )
            params["search_q"] = f"%{filters.query.strip()}%"

        where_str = " AND ".join(where_conditions)

        count_query = text(f"SELECT COUNT(*) FROM public.leads WHERE {where_str}")  # noqa: S608
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT * FROM public.leads
            WHERE {where_str}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count

    async def assign_lead(
        self,
        tenant_id: UUID,
        lead_id: UUID,
        sales_agent_id: UUID,
        assigned_by: UUID | None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Assign sales agent to lead and record assignment history."""
        await self.session.execute(
            text(
                """
                UPDATE public.sales_assignments
                SET is_primary = false, unassigned_at = NOW(), updated_at = NOW()
                WHERE tenant_id = :tenant_id AND lead_id = :lead_id AND is_primary = true
                """
            ),
            {"tenant_id": tenant_id, "lead_id": lead_id},
        )

        await self.session.execute(
            text(
                """
                INSERT INTO public.sales_assignments (
                    tenant_id, lead_id, sales_agent_id, assigned_by,
                    assignment_type, is_primary, reason, assigned_at
                ) VALUES (
                    :tenant_id, :lead_id, :sales_agent_id, :assigned_by,
                    'manual'::public.assignment_type, true, :reason, NOW()
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "sales_agent_id": sales_agent_id,
                "assigned_by": assigned_by,
                "reason": reason,
            },
        )

        result = await self.session.execute(
            text(
                """
                UPDATE public.leads
                SET assigned_sales_agent_id = :sales_agent_id, updated_at = NOW()
                WHERE id = :lead_id AND tenant_id = :tenant_id AND deleted_at IS NULL
                RETURNING *
                """
            ),
            {"tenant_id": tenant_id, "lead_id": lead_id, "sales_agent_id": sales_agent_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def add_property_interest(
        self, tenant_id: UUID, lead_id: UUID, data: LeadPropertyInterestCreate
    ) -> dict[str, Any]:
        """Add property interest to a lead."""
        if data.is_primary:
            await self.session.execute(
                text(
                    """
                    UPDATE public.lead_property_interests
                    SET is_primary = false, updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND lead_id = :lead_id
                    """
                ),
                {"tenant_id": tenant_id, "lead_id": lead_id},
            )

        query = text(
            """
            INSERT INTO public.lead_property_interests (
                tenant_id, lead_id, project_id, property_id,
                interest_level, is_primary, notes
            ) VALUES (
                :tenant_id, :lead_id, :project_id, :property_id,
                CAST(:interest_level AS public.interest_level), :is_primary, :notes
            )
            ON CONFLICT (lead_id, project_id, property_id) DO UPDATE SET
                interest_level = EXCLUDED.interest_level,
                is_primary = EXCLUDED.is_primary,
                notes = EXCLUDED.notes,
                updated_at = NOW()
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "project_id": data.project_id,
            "property_id": data.property_id,
            "interest_level": data.interest_level,
            "is_primary": data.is_primary,
            "notes": data.notes,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def remove_property_interest(
        self, tenant_id: UUID, lead_id: UUID, interest_id: UUID
    ) -> bool:
        """Remove a property interest entry."""
        query = text(
            """
            DELETE FROM public.lead_property_interests
            WHERE id = :interest_id AND lead_id = :lead_id AND tenant_id = :tenant_id
            """
        )
        result = await self.session.execute(
            query, {"interest_id": interest_id, "lead_id": lead_id, "tenant_id": tenant_id}
        )
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount > 0)

    async def list_property_interests(self, tenant_id: UUID, lead_id: UUID) -> list[dict[str, Any]]:
        """List all property interests for a lead."""
        query = text(
            """
            SELECT * FROM public.lead_property_interests
            WHERE lead_id = :lead_id AND tenant_id = :tenant_id
            ORDER BY is_primary DESC, created_at DESC
            """
        )
        result = await self.session.execute(query, {"lead_id": lead_id, "tenant_id": tenant_id})
        return [dict(r) for r in result.mappings().all()]

    async def add_note(
        self,
        tenant_id: UUID,
        lead_id: UUID,
        customer_id: UUID,
        author_id: UUID,
        note_text: str,
        is_pinned: bool = False,
    ) -> dict[str, Any]:
        """Add note to lead."""
        query = text(
            """
            INSERT INTO public.lead_notes (
                tenant_id, lead_id, customer_id, author_id, note_text, is_pinned
            ) VALUES (
                :tenant_id, :lead_id, :customer_id, :author_id, :note_text, :is_pinned
            )
            RETURNING *
            """
        )
        result = await self.session.execute(
            query,
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "customer_id": customer_id,
                "author_id": author_id,
                "note_text": note_text,
                "is_pinned": is_pinned,
            },
        )
        return dict(result.mappings().one())

    async def list_notes(self, tenant_id: UUID, lead_id: UUID) -> list[dict[str, Any]]:
        """List notes for a lead."""
        query = text(
            """
            SELECT * FROM public.lead_notes
            WHERE lead_id = :lead_id AND tenant_id = :tenant_id
            ORDER BY is_pinned DESC, created_at DESC
            """
        )
        result = await self.session.execute(query, {"lead_id": lead_id, "tenant_id": tenant_id})
        return [dict(r) for r in result.mappings().all()]
