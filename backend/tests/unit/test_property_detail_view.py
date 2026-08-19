"""Unit tests for GET /properties/{id}/detail aggregation, focused on the
no-N+1-query guarantee for co-owners across multiple ownership periods."""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundError
from app.properties.repository import PropertyRepository
from app.properties.service import PropertyService


def _base_property_row(property_id, tenant_id, project_id) -> dict:  # type: ignore[no-untyped-def]
    return {
        "id": property_id,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "property_type_id": uuid4(),
        "property_code": "P-101",
        "unit_number": None,
        "block": None,
        "floor_number": None,
        "plot_area": None,
        "built_up_area": None,
        "carpet_area": None,
        "super_built_up_area": None,
        "area_unit": "sqft",
        "bedrooms": None,
        "bathrooms": None,
        "balconies": None,
        "parking_covered": 0,
        "parking_open": 0,
        "facing": None,
        "is_corner": False,
        "is_park_facing": False,
        "is_road_facing": False,
        "base_price": None,
        "offer_price": None,
        "price_per_unit": None,
        "currency": "INR",
        "status": "sold",
        "is_public": False,
        "is_featured": False,
        "custom_attributes": {},
        "construction_status": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


@pytest.mark.asyncio
async def test_get_property_detail_raises_not_found_for_unknown_property() -> None:
    """Verify the detail endpoint 404s cleanly for a non-existent property."""
    session = AsyncMock()
    service = PropertyService(session)
    service.repository.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError):
        await service.get_property_detail(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_get_property_detail_batches_co_owners_in_one_query_for_all_periods() -> None:
    """Verify co-owners for a multi-period ownership chain are fetched via a
    single batched query (ANY(:ownership_ids)), not one query per period --
    the repository method itself only issues 2 queries total regardless of
    how many ownership periods exist."""
    tenant_id = uuid4()
    property_id = uuid4()
    project_id = uuid4()

    session = AsyncMock()
    service = PropertyService(session)
    service.repository.get_by_id = AsyncMock(
        return_value=_base_property_row(property_id, tenant_id, project_id)
    )
    service.repository.get_project_context = AsyncMock(return_value=None)
    service.repository.get_construction_milestones = AsyncMock(return_value=[])
    service.repository.get_current_prices = AsyncMock(return_value=[])
    service.repository.get_open_resale_listing = AsyncMock(return_value=None)

    ownership_id_1, ownership_id_2, ownership_id_3 = uuid4(), uuid4(), uuid4()
    customer_1, customer_2 = uuid4(), uuid4()

    # Simulate what get_ownership_history_with_co_owners returns after its own
    # internal 2-query batching (repository-level batching is exercised directly
    # below; here we verify the service correctly threads co_owners per period).
    get_history_mock = AsyncMock(
        return_value=[
            {
                "id": ownership_id_1,
                "customer_id": customer_1,
                "purchase_purpose": "end_use",
                "previous_ownership_id": None,
                "ownership_start_date": date(2020, 1, 1),
                "ownership_end_date": date(2022, 1, 1),
                "ownership_status": "active",
                "co_owners": [],
            },
            {
                "id": ownership_id_2,
                "customer_id": customer_2,
                "purchase_purpose": "investment",
                "previous_ownership_id": ownership_id_1,
                "ownership_start_date": date(2022, 1, 1),
                "ownership_end_date": None,
                "ownership_status": "active",
                "co_owners": [
                    {
                        "ownership_id": ownership_id_2,
                        "customer_id": uuid4(),
                        "role": "spouse",
                        "share_percentage": Decimal("40"),
                    }
                ],
            },
        ]
    )
    service.repository.get_ownership_history_with_co_owners = get_history_mock

    detail = await service.get_property_detail(tenant_id, property_id)

    # Exactly one call covers the whole ownership+co-owner fetch for the property,
    # regardless of how many ownership periods it has -- no per-row looping.
    get_history_mock.assert_awaited_once_with(tenant_id, property_id)

    assert len(detail.ownership_history) == 2
    assert detail.current_owner is not None
    assert detail.current_owner.customer_id == customer_2
    assert len(detail.current_owner.co_owners) == 1
    assert detail.current_owner.co_owners[0].role == "spouse"
    assert len(detail.ownership_history[0].co_owners) == 0
    assert ownership_id_3 not in [p.id for p in detail.ownership_history]  # sanity: unused id


@pytest.mark.asyncio
async def test_repository_batches_co_owner_fetch_in_exactly_two_queries() -> None:
    """Verify the repository's SQL layer for a 3-period ownership chain issues
    exactly 2 session.execute calls: one for the periods, one ANY(...) batch
    fetch for all their co-owners -- never one query per ownership period."""
    session = AsyncMock()
    repo = PropertyRepository(session)

    tenant_id = uuid4()
    property_id = uuid4()
    ownership_ids = [uuid4(), uuid4(), uuid4()]

    ownership_result = MagicMock()
    ownership_result.mappings.return_value.all.return_value = [
        {
            "id": oid,
            "customer_id": uuid4(),
            "purchase_purpose": None,
            "previous_ownership_id": None,
            "ownership_start_date": date(2020, 1, 1),
            "ownership_end_date": date(2021, 1, 1),
            "ownership_status": "active",
        }
        for oid in ownership_ids
    ]

    co_owner_result = MagicMock()
    co_owner_result.mappings.return_value.all.return_value = []

    session.execute.side_effect = [ownership_result, co_owner_result]

    periods = await repo.get_ownership_history_with_co_owners(tenant_id, property_id)

    assert session.execute.call_count == 2
    assert len(periods) == 3
    assert all(p["co_owners"] == [] for p in periods)
