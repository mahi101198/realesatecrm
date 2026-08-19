"""Appointment Repository for PostgreSQL database access."""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.schemas import (
    AppointmentCreate,
    AppointmentFilter,
    AppointmentUpdate,
)
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class AppointmentRepository:
    """Repository handling database access for Appointments."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check_availability(
        self,
        tenant_id: UUID | None,
        sales_agent_id: UUID,
        scheduled_at: datetime,
        duration_minutes: int = 60,
    ) -> bool:
        """Check if sales agent has availability for a site visit time slot."""
        from datetime import timedelta

        end_time_dt = scheduled_at + timedelta(minutes=duration_minutes)
        query = text(
            """
            SELECT COUNT(*) FROM public.appointments
            WHERE sales_agent_id = :agent_id
              AND status NOT IN ('cancelled', 'rescheduled')
              AND appointment_range && tstzrange(:start_time, :end_time, '[)')
              AND (:tenant_id::uuid IS NULL OR tenant_id = :tenant_id)
            """
        )
        res = await self.session.execute(
            query,
            {
                "agent_id": sales_agent_id,
                "start_time": scheduled_at,
                "end_time": end_time_dt,
                "tenant_id": tenant_id,
            },
        )
        conflict_count = res.scalar_one()
        return bool(conflict_count == 0)

    async def book_site_visit(
        self, tenant_id: UUID, created_by: UUID | None, data: AppointmentCreate
    ) -> UUID:
        """Atomically book site visit using stored procedure public.book_site_visit."""
        query = text(
            """
            SELECT public.book_site_visit(
                :p_tenant_id,
                :p_customer_id,
                :p_lead_id,
                :p_project_id,
                :p_property_id,
                :p_sales_agent_id,
                :p_scheduled_at,
                :p_duration_minutes,
                :p_source::public.appointment_source,
                :p_related_call_id,
                :p_notes,
                :p_created_by
            )
            """
        )
        params = {
            "p_tenant_id": tenant_id,
            "p_customer_id": data.customer_id,
            "p_lead_id": data.lead_id,
            "p_project_id": data.project_id,
            "p_property_id": data.property_id,
            "p_sales_agent_id": data.sales_agent_id,
            "p_scheduled_at": data.scheduled_at,
            "p_duration_minutes": data.duration_minutes,
            "p_source": data.source,
            "p_related_call_id": data.related_call_id,
            "p_notes": data.notes,
            "p_created_by": created_by,
        }
        result = await self.session.execute(query, params)
        appt_id: UUID = result.scalar_one()
        return appt_id

    async def get_by_id(
        self, tenant_id: UUID | None, appointment_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch appointment by ID."""
        if tenant_id is not None:
            query = text(
                """
                SELECT * FROM public.appointments
                WHERE id = :id AND tenant_id = :tenant_id
                """
            )
            params = {"id": appointment_id, "tenant_id": tenant_id}
        else:
            query = text(
                """
                SELECT * FROM public.appointments
                WHERE id = :id
                """
            )
            params = {"id": appointment_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update(
        self, tenant_id: UUID | None, appointment_id: UUID, data: AppointmentUpdate
    ) -> dict[str, Any] | None:
        """Update appointment record."""
        data_dict = data.model_dump(exclude_unset=True)
        if not data_dict:
            return await self.get_by_id(tenant_id, appointment_id)

        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": appointment_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        if "status" in data_dict:
            set_clauses.append("status = :val_status::public.appointment_status")
            params["val_status"] = data_dict.pop("status")

            val_status = params["val_status"]
            if val_status == "confirmed":
                set_clauses.append("confirmed_at = NOW()")
            elif val_status == "completed":
                set_clauses.append("completed_at = NOW()")

        for key, value in data_dict.items():
            param_key = f"val_{key}"
            set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :id"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.appointments
            SET {set_str}, updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def cancel(
        self,
        tenant_id: UUID | None,
        appointment_id: UUID,
        cancelled_by: UUID | None,
        reason: str | None,
    ) -> dict[str, Any] | None:
        """Cancel an appointment."""
        where_clause = "WHERE id = :id AND status NOT IN ('cancelled', 'completed')"
        params: dict[str, Any] = {
            "id": appointment_id,
            "cancelled_by": cancelled_by,
            "reason": reason,
        }
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            UPDATE public.appointments
            SET status = 'cancelled'::public.appointment_status,
                cancelled_at = NOW(),
                cancelled_by = :cancelled_by,
                cancellation_reason = :reason,
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
        filters: AppointmentFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search appointments with filtering and pagination."""
        where_conditions: list[str] = ["1=1"]
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

        if filters.lead_id:
            where_conditions.append("lead_id = :filter_lead_id")
            params["filter_lead_id"] = filters.lead_id

        if filters.sales_agent_id:
            where_conditions.append("sales_agent_id = :filter_agent_id")
            params["filter_agent_id"] = filters.sales_agent_id

        if filters.project_id:
            where_conditions.append("project_id = :filter_project_id")
            params["filter_project_id"] = filters.project_id

        if filters.status:
            where_conditions.append("status = :filter_status::public.appointment_status")
            params["filter_status"] = filters.status

        if filters.start_date:
            where_conditions.append("scheduled_at >= :start_date")
            params["start_date"] = filters.start_date

        if filters.end_date:
            where_conditions.append("scheduled_at <= :end_date")
            params["end_date"] = filters.end_date

        where_str = " AND ".join(where_conditions)

        count_query = text(f"SELECT COUNT(*) FROM public.appointments WHERE {where_str}")  # noqa: S608
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT * FROM public.appointments
            WHERE {where_str}
            ORDER BY scheduled_at ASC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count
