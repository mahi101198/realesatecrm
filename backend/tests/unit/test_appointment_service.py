"""Unit tests for Appointment domain schemas."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.appointments.schemas import AppointmentCreate


def test_appointment_create_schema() -> None:
    """Verify AppointmentCreate schema validation."""
    cust_id = uuid4()
    future_time = datetime.now(UTC) + timedelta(days=1)
    appt = AppointmentCreate(
        customer_id=cust_id,
        scheduled_at=future_time,
        duration_minutes=60,
        source="ai_agent",
    )
    assert appt.customer_id == cust_id
    assert appt.duration_minutes == 60
    assert appt.source == "ai_agent"

    with pytest.raises(ValidationError):
        AppointmentCreate(
            customer_id=cust_id,
            scheduled_at=future_time,
            duration_minutes=0,  # Invalid duration < 5
        )
