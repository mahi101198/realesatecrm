"""Unit tests for Customer domain logic and schemas."""

import pytest
from pydantic import ValidationError

from app.customers.schemas import CustomerCreate, CustomerUpdate, normalize_phone


def test_phone_normalization() -> None:
    """Verify phone normalization formats Indian phone numbers consistently."""
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("919876543210") == "+919876543210"
    assert normalize_phone("+1 202 555 0123") == "+12025550123"

    with pytest.raises(ValueError, match="Invalid phone number format."):
        normalize_phone("abc")


def test_customer_create_schema_validation() -> None:
    """Verify CustomerCreate schema validates required fields."""
    valid_data = CustomerCreate(
        full_name="Rajesh Kumar",
        phone="9876543210",
        email="rajesh@example.com",
    )
    assert valid_data.full_name == "Rajesh Kumar"
    assert valid_data.phone == "+919876543210"
    assert valid_data.email == "rajesh@example.com"

    with pytest.raises(ValidationError):
        CustomerCreate(full_name="", phone="9876543210")


def test_customer_update_schema_partial() -> None:
    """Verify CustomerUpdate schema supports optional field updates."""
    update_data = CustomerUpdate(city="Gurgaon")
    dumped = update_data.model_dump(exclude_unset=True)
    assert dumped == {"city": "Gurgaon"}
