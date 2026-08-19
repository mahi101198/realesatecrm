"""Appointment Business Service Layer."""

import logging
from datetime import datetime
from math import ceil
from uuid import UUID

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.repository import AppointmentRepository
from app.appointments.schemas import (
    AppointmentCancelRequest,
    AppointmentCreate,
    AppointmentFilter,
    AppointmentResponse,
    AppointmentUpdate,
)
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.schemas import PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)


class AppointmentService:
    """Service for managing appointments and site visits with double-booking prevention."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = AppointmentRepository(session)

    async def check_availability(
        self,
        tenant_id: UUID | None,
        sales_agent_id: UUID,
        scheduled_at: datetime,
        duration_minutes: int = 60,
    ) -> bool:
        """Check if sales agent has availability for a site visit time slot."""
        return await self.repository.check_availability(
            tenant_id, sales_agent_id, scheduled_at, duration_minutes
        )

    async def schedule_site_visit(
        self, tenant_id: UUID, created_by: UUID | None, data: AppointmentCreate
    ) -> AppointmentResponse:
        """Schedule a site visit appointment with double-booking prevention."""
        try:
            appt_id = await self.repository.book_site_visit(tenant_id, created_by, data)
            row = await self.repository.get_by_id(tenant_id, appt_id)
            if not row:
                raise RuntimeError("Appointment scheduled but row lookup failed.")
            return AppointmentResponse.model_validate(row)
        except DBAPIError as e:
            orig_msg = str(e.orig) if hasattr(e, "orig") else str(e)
            logger.warning(f"Book site visit DB Exception: {orig_msg}")

            if "P0001" in orig_msg:
                raise ValidationError(
                    message="Appointment time must be in future and duration positive.",
                    code="INVALID_APPOINTMENT_TIME",
                ) from e
            if "P0002" in orig_msg:
                raise NotFoundError(
                    message=(
                        "Referenced customer, lead, project, property, "
                        "or sales agent was not found in this tenant."
                    ),
                    code="RESOURCE_NOT_FOUND",
                ) from e
            if "P0003" in orig_msg:
                raise ConflictError(
                    message="Concurrent booking attempt for this agent. Please retry.",
                    code="CONCURRENT_BOOKING_ATTEMPT",
                ) from e
            if (
                "P0004" in orig_msg
                or "exclusion_violation" in orig_msg
                or "excl_appointments_agent_no_overlap" in orig_msg
            ):
                raise ConflictError(
                    message="Sales agent has an appointment overlapping this slot.",
                    code="SITE_VISIT_SLOT_UNAVAILABLE",
                ) from e
            raise ConflictError(
                message="Could not schedule site visit due to slot conflict.",
                code="SITE_VISIT_SLOT_UNAVAILABLE",
            ) from e

    async def get_appointment(
        self, tenant_id: UUID | None, appointment_id: UUID
    ) -> AppointmentResponse:
        """Fetch appointment details."""
        row = await self.repository.get_by_id(tenant_id, appointment_id)
        if not row:
            raise NotFoundError(
                message=f"Appointment with ID '{appointment_id}' was not found.",
                code="APPOINTMENT_NOT_FOUND",
            )
        return AppointmentResponse.model_validate(row)

    async def update_appointment(
        self, tenant_id: UUID | None, appointment_id: UUID, data: AppointmentUpdate
    ) -> AppointmentResponse:
        """Update appointment details."""
        existing = await self.repository.get_by_id(tenant_id, appointment_id)
        if not existing:
            raise NotFoundError(
                message=f"Appointment with ID '{appointment_id}' was not found.",
                code="APPOINTMENT_NOT_FOUND",
            )

        updated_row = await self.repository.update(tenant_id, appointment_id, data)
        if not updated_row:
            raise NotFoundError(
                message=f"Appointment with ID '{appointment_id}' was not found.",
                code="APPOINTMENT_NOT_FOUND",
            )
        return AppointmentResponse.model_validate(updated_row)

    reschedule_appointment = update_appointment


    async def cancel_appointment(
        self,
        tenant_id: UUID | None,
        appointment_id: UUID,
        cancelled_by: UUID | None,
        data: AppointmentCancelRequest,
    ) -> AppointmentResponse:
        """Cancel an appointment."""
        existing = await self.repository.get_by_id(tenant_id, appointment_id)
        if not existing:
            raise NotFoundError(
                message=f"Appointment with ID '{appointment_id}' was not found.",
                code="APPOINTMENT_NOT_FOUND",
            )

        if existing["status"] in ("cancelled", "completed"):
            raise ConflictError(
                message=f"Appointment '{appointment_id}' is already '{existing['status']}'.",
                code="INVALID_APPOINTMENT_STATE",
            )

        cancelled_row = await self.repository.cancel(
            tenant_id, appointment_id, cancelled_by, data.reason
        )
        if not cancelled_row:
            raise NotFoundError(
                message=f"Appointment with ID '{appointment_id}' was not found.",
                code="APPOINTMENT_NOT_FOUND",
            )
        return AppointmentResponse.model_validate(cancelled_row)

    async def list_appointments(
        self,
        tenant_id: UUID | None,
        filters: AppointmentFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[AppointmentResponse]:
        """Search and list appointments with pagination."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [AppointmentResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[AppointmentResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )
