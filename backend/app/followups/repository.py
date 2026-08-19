"""Follow-up Repository for database operations."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.followups.schemas import FollowUpCreate, FollowUpFilter, FollowUpUpdate
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class FollowUpRepository:
    """Repository handling database access for Follow-up tasks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, tenant_id: UUID, customer_id: UUID, created_by: UUID | None, data: FollowUpCreate
    ) -> dict[str, Any]:
        """Insert a new follow-up task record."""
        query = text(
            """
            INSERT INTO public.follow_ups (
                tenant_id, lead_id, customer_id, related_call_id,
                follow_up_type, scheduled_at, status, reason, notes,
                assigned_sales_agent_id, created_by, metadata
            ) VALUES (
                :tenant_id, :lead_id, :customer_id, :related_call_id,
                :follow_up_type::public.follow_up_type, :scheduled_at,
                'pending'::public.follow_up_status,
                :reason, :notes, :assigned_sales_agent_id, :created_by, :metadata
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "lead_id": data.lead_id,
            "customer_id": customer_id,
            "related_call_id": data.related_call_id,
            "follow_up_type": data.follow_up_type,
            "scheduled_at": data.scheduled_at,
            "reason": data.reason,
            "notes": data.notes,
            "assigned_sales_agent_id": data.assigned_sales_agent_id,
            "created_by": created_by,
            "metadata": data.metadata or {},
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def get_by_id(self, tenant_id: UUID | None, followup_id: UUID) -> dict[str, Any] | None:
        """Fetch follow-up by ID."""
        if tenant_id is not None:
            query = text(
                """
                SELECT * FROM public.follow_ups
                WHERE id = :id AND tenant_id = :tenant_id
                """
            )
            params = {"id": followup_id, "tenant_id": tenant_id}
        else:
            query = text(
                """
                SELECT * FROM public.follow_ups
                WHERE id = :id
                """
            )
            params = {"id": followup_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update(
        self, tenant_id: UUID | None, followup_id: UUID, data: FollowUpUpdate
    ) -> dict[str, Any] | None:
        """Update follow-up details."""
        data_dict = data.model_dump(exclude_unset=True)
        if not data_dict:
            return await self.get_by_id(tenant_id, followup_id)

        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": followup_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        enum_casts = {
            "follow_up_type": "public.follow_up_type",
            "status": "public.follow_up_status",
        }

        for key, value in data_dict.items():
            param_key = f"val_{key}"
            if key in enum_casts:
                set_clauses.append(f"{key} = :{param_key}::{enum_casts[key]}")
            else:
                set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :id"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.follow_ups
            SET {set_str}, updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def complete(
        self,
        tenant_id: UUID | None,
        followup_id: UUID,
        completed_by: UUID | None,
        completion_notes: str | None,
    ) -> dict[str, Any] | None:
        """Mark follow-up completed."""
        where_clause = "WHERE id = :id"
        params: dict[str, Any] = {
            "id": followup_id,
            "completed_by": completed_by,
            "completion_notes": completion_notes,
        }
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            UPDATE public.follow_ups
            SET status = 'completed'::public.follow_up_status,
                completed_at = NOW(),
                completed_by = :completed_by,
                completion_notes = :completion_notes,
                updated_at = NOW()
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
        filters: FollowUpFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search follow-ups with filtering and pagination."""
        where_conditions: list[str] = ["1=1"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.lead_id:
            where_conditions.append("lead_id = :filter_lead_id")
            params["filter_lead_id"] = filters.lead_id

        if filters.customer_id:
            where_conditions.append("customer_id = :filter_customer_id")
            params["filter_customer_id"] = filters.customer_id

        if filters.assigned_sales_agent_id:
            where_conditions.append("assigned_sales_agent_id = :filter_agent_id")
            params["filter_agent_id"] = filters.assigned_sales_agent_id

        if filters.status:
            where_conditions.append("status = :filter_status::public.follow_up_status")
            params["filter_status"] = filters.status

        if filters.follow_up_type:
            where_conditions.append("follow_up_type = :filter_type::public.follow_up_type")
            params["filter_type"] = filters.follow_up_type

        if filters.scheduled_before:
            where_conditions.append("scheduled_at <= :sched_before")
            params["sched_before"] = filters.scheduled_before

        if filters.scheduled_after:
            where_conditions.append("scheduled_at >= :sched_after")
            params["sched_after"] = filters.scheduled_after

        where_str = " AND ".join(where_conditions)

        count_query = text(f"SELECT COUNT(*) FROM public.follow_ups WHERE {where_str}")  # noqa: S608
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT * FROM public.follow_ups
            WHERE {where_str}
            ORDER BY scheduled_at ASC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count
