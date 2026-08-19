"""Unit tests for Property domain schemas and search filters."""

from decimal import Decimal

from app.properties.schemas import PropertyReserveRequest, PropertySearchFilter


def test_property_search_filter_schema() -> None:
    """Verify property search filter validation."""
    filters = PropertySearchFilter(
        status="available",
        min_budget=Decimal("4000000"),
        max_budget=Decimal("8000000"),
        bedrooms=3,
    )
    assert filters.status == "available"
    assert filters.min_budget == Decimal("4000000")
    assert filters.bedrooms == 3


def test_property_reserve_request_schema() -> None:
    """Verify property reservation request payload validation."""
    req = PropertyReserveRequest(
        new_status="reserved",
        reason="Token amount received by buyer",
    )
    assert req.new_status == "reserved"
    assert req.reason == "Token amount received by buyer"
