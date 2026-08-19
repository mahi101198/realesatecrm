"""Concurrency/idempotency-style tests for the sale-creation ownership-transfer
transaction (PropertySaleService.create_sale), mirroring the mocked-repository
style used for the existing property-reservation tests
(tests/unit/test_phase4_idempotency_concurrency.py::test_site_visit_double_booking_prevention).

Real concurrent-transaction behavior (two DB connections racing on the same
SELECT ... FOR UPDATE row lock) is provided by PostgreSQL itself, the same way
the existing reserve_property() function's concurrency guarantee is provided
by PostgreSQL rather than re-tested at the Python level. What IS tested here,
at the service layer, with a mocked repository: the decision logic each branch
of that transaction takes -- the business rule that a property must already be
reserved/hold before it can be sold, the primary-sale vs resale branching
(previous_ownership_id chain), booking/lead auto-conversion, and that a DB-level
conflict (e.g. another transaction winning the row lock) surfaces as a clean
ConflictError rather than a raw DB exception.
"""

from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.sales.schemas import PropertySaleCreate, PropertySaleUpdate
from app.sales.service import PropertySaleService


@asynccontextmanager
async def _noop_atomic(_session):  # type: ignore[no-untyped-def]
    """No-op stand-in for app.db.transaction.atomic() -- the transaction
    wrapper itself is generic infrastructure already covered elsewhere;
    these tests focus on the ownership-transfer decision logic it wraps."""
    yield _session


def _service_with_mocked_deps() -> tuple[PropertySaleService, AsyncMock]:
    session = AsyncMock()
    customer_check = MagicMock()
    customer_check.scalar_one_or_none.return_value = uuid4()
    session.execute.return_value = customer_check

    service = PropertySaleService(session)
    return service, session


@pytest.mark.asyncio
async def test_create_sale_rejects_property_never_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Selling a 'draft'/'available' property that was never reserved is a
    business-rule violation, not a silent allow."""
    service, _ = _service_with_mocked_deps()
    property_id = uuid4()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(
        return_value={"id": property_id, "status": "available"}
    )

    data = PropertySaleCreate(
        property_id=property_id, customer_id=uuid4(), sale_amount=Decimal("5000000")
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.create_sale(uuid4(), uuid4(), data)
    assert exc_info.value.code == "PROPERTY_NOT_RESERVED"


@pytest.mark.asyncio
async def test_create_sale_rejects_unknown_property(monkeypatch: pytest.MonkeyPatch) -> None:
    """Locking a non-existent (or cross-tenant) property raises NotFoundError."""
    service, _ = _service_with_mocked_deps()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(return_value=None)

    data = PropertySaleCreate(
        property_id=uuid4(), customer_id=uuid4(), sale_amount=Decimal("5000000")
    )

    with pytest.raises(NotFoundError):
        await service.create_sale(uuid4(), uuid4(), data)


@pytest.mark.asyncio
async def test_create_sale_primary_sale_has_no_previous_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no active ownership row exists, this is a primary sale:
    previous_ownership_id must be None and no ownership row is closed."""
    service, _ = _service_with_mocked_deps()
    property_id = uuid4()
    sale_id = uuid4()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(
        return_value={"id": property_id, "status": "reserved"}
    )
    service.repository.insert_sale = AsyncMock(
        return_value={
            "id": sale_id,
            "tenant_id": uuid4(),
            "booking_id": None,
            "property_id": property_id,
            "customer_id": uuid4(),
            "sale_date": "2026-08-15",
            "sale_amount": Decimal("5000000"),
            "discount_amount": Decimal("0"),
            "tax_amount": Decimal("0"),
            "sale_status": "active",
            "created_by": None,
            "created_at": "2026-08-15T00:00:00Z",
            "updated_at": "2026-08-15T00:00:00Z",
        }
    )
    service.repository.get_active_ownership_for_update = AsyncMock(return_value=None)
    service.repository.close_ownership = AsyncMock()
    service.repository.close_open_resale_listing_for_ownership = AsyncMock()
    insert_ownership_mock = AsyncMock(return_value={"id": uuid4()})
    service.repository.insert_ownership = insert_ownership_mock
    service.repository.update_property_status = AsyncMock()

    data = PropertySaleCreate(
        property_id=property_id, customer_id=uuid4(), sale_amount=Decimal("5000000")
    )
    result = await service.create_sale(uuid4(), uuid4(), data)

    assert result.id == sale_id
    service.repository.close_ownership.assert_not_called()
    insert_ownership_mock.assert_awaited_once()
    assert insert_ownership_mock.call_args.kwargs["previous_ownership_id"] is None
    service.repository.update_property_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_sale_resale_chains_previous_ownership_and_converts_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an active ownership row exists, this is a resale: it is closed out,
    the new row's previous_ownership_id chains to it, and any open resale
    listing tied to it is auto-converted."""
    service, _ = _service_with_mocked_deps()
    property_id = uuid4()
    prior_ownership_id = uuid4()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(
        return_value={"id": property_id, "status": "hold"}
    )
    service.repository.insert_sale = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": uuid4(),
            "booking_id": None,
            "property_id": property_id,
            "customer_id": uuid4(),
            "sale_date": "2026-08-15",
            "sale_amount": Decimal("7200000"),
            "discount_amount": Decimal("0"),
            "tax_amount": Decimal("0"),
            "sale_status": "active",
            "created_by": None,
            "created_at": "2026-08-15T00:00:00Z",
            "updated_at": "2026-08-15T00:00:00Z",
        }
    )
    service.repository.get_active_ownership_for_update = AsyncMock(
        return_value={"id": prior_ownership_id, "ownership_end_date": None}
    )
    close_ownership_mock = AsyncMock()
    service.repository.close_ownership = close_ownership_mock
    close_listing_mock = AsyncMock()
    service.repository.close_open_resale_listing_for_ownership = close_listing_mock
    insert_ownership_mock = AsyncMock(return_value={"id": uuid4()})
    service.repository.insert_ownership = insert_ownership_mock
    service.repository.update_property_status = AsyncMock()

    data = PropertySaleCreate(
        property_id=property_id, customer_id=uuid4(), sale_amount=Decimal("7200000")
    )
    await service.create_sale(uuid4(), uuid4(), data)

    close_ownership_mock.assert_awaited_once_with(prior_ownership_id, "2026-08-15")
    close_listing_mock.assert_awaited_once()
    assert close_listing_mock.call_args.args[1] == prior_ownership_id
    assert insert_ownership_mock.call_args.kwargs["previous_ownership_id"] == prior_ownership_id


@pytest.mark.asyncio
async def test_create_sale_converts_booking_and_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a booking_id is given, it must be active, belong to the same property,
    and get flipped to 'converted'; its originating lead (if any) is marked converted."""
    service, _ = _service_with_mocked_deps()
    property_id = uuid4()
    booking_id = uuid4()
    lead_id = uuid4()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(
        return_value={"id": property_id, "status": "reserved"}
    )
    service.repository.lock_booking_for_update = AsyncMock(
        return_value={
            "id": booking_id,
            "property_id": property_id,
            "booking_status": "active",
            "lead_id": lead_id,
        }
    )
    service.repository.insert_sale = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": uuid4(),
            "booking_id": booking_id,
            "property_id": property_id,
            "customer_id": uuid4(),
            "sale_date": "2026-08-15",
            "sale_amount": Decimal("5000000"),
            "discount_amount": Decimal("0"),
            "tax_amount": Decimal("0"),
            "sale_status": "active",
            "created_by": None,
            "created_at": "2026-08-15T00:00:00Z",
            "updated_at": "2026-08-15T00:00:00Z",
        }
    )
    mark_booking_mock = AsyncMock()
    service.repository.mark_booking_converted = mark_booking_mock
    mark_lead_mock = AsyncMock()
    service.repository.mark_lead_converted = mark_lead_mock
    service.repository.get_active_ownership_for_update = AsyncMock(return_value=None)
    service.repository.insert_ownership = AsyncMock(return_value={"id": uuid4()})
    service.repository.update_property_status = AsyncMock()

    data = PropertySaleCreate(
        property_id=property_id,
        customer_id=uuid4(),
        booking_id=booking_id,
        sale_amount=Decimal("5000000"),
    )
    await service.create_sale(uuid4(), uuid4(), data)

    mark_booking_mock.assert_awaited_once()
    assert mark_booking_mock.call_args.args[1] == booking_id
    mark_lead_mock.assert_awaited_once()
    assert mark_lead_mock.call_args.args[1] == lead_id


@pytest.mark.asyncio
async def test_create_sale_rejects_booking_for_different_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A booking_id referencing a different property than being sold is rejected."""
    service, _ = _service_with_mocked_deps()
    property_id = uuid4()
    other_property_id = uuid4()
    booking_id = uuid4()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(
        return_value={"id": property_id, "status": "reserved"}
    )
    service.repository.lock_booking_for_update = AsyncMock(
        return_value={
            "id": booking_id,
            "property_id": other_property_id,
            "booking_status": "active",
            "lead_id": None,
        }
    )

    data = PropertySaleCreate(
        property_id=property_id,
        customer_id=uuid4(),
        booking_id=booking_id,
        sale_amount=Decimal("5000000"),
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.create_sale(uuid4(), uuid4(), data)
    assert exc_info.value.code == "BOOKING_PROPERTY_MISMATCH"


@pytest.mark.asyncio
async def test_create_sale_maps_dbapi_conflict_to_clean_conflict_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB-level conflict during the transaction (e.g. a concurrent transaction
    winning the row lock and invalidating an assumption) surfaces as a clean
    ConflictError, not a raw DBAPIError leaking to the client."""
    service, _ = _service_with_mocked_deps()
    property_id = uuid4()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(
        return_value={"id": property_id, "status": "reserved"}
    )
    service.repository.insert_sale = AsyncMock(
        side_effect=DBAPIError("insert", {}, Exception("simulated conflict"))
    )

    data = PropertySaleCreate(
        property_id=property_id, customer_id=uuid4(), sale_amount=Decimal("5000000")
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.create_sale(uuid4(), uuid4(), data)
    assert exc_info.value.code == "SALE_CREATION_CONFLICT"


@pytest.mark.asyncio
async def test_create_sale_maps_bad_purchase_purpose_to_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed purchase_purpose value is a data problem, not a
    concurrency problem -- retrying would fail identically, so it must get
    its own specific ValidationError rather than the generic 'please retry'
    ConflictError framing used for genuine lock-contention conflicts."""
    service, _ = _service_with_mocked_deps()
    property_id = uuid4()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(
        return_value={"id": property_id, "status": "reserved"}
    )
    service.repository.insert_sale = AsyncMock(
        side_effect=DBAPIError(
            "insert", {}, Exception('invalid input value for enum public.purpose: "bogus"')
        )
    )

    data = PropertySaleCreate(
        property_id=property_id, customer_id=uuid4(), sale_amount=Decimal("5000000")
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.create_sale(uuid4(), uuid4(), data)
    assert exc_info.value.code == "INVALID_FIELD_VALUE"


@pytest.mark.asyncio
async def test_create_sale_maps_check_violation_to_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CHECK constraint violation during sale creation (e.g. a negative
    amount that somehow reached the DB) maps to a clean ValidationError."""
    service, _ = _service_with_mocked_deps()
    property_id = uuid4()

    monkeypatch.setattr("app.sales.service.atomic", _noop_atomic)
    service.repository.lock_property_for_update = AsyncMock(
        return_value={"id": property_id, "status": "reserved"}
    )
    service.repository.insert_sale = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception(
                'new row for relation "property_sales" violates check '
                'constraint "chk_property_sales_amount"'
            ),
        )
    )

    data = PropertySaleCreate(
        property_id=property_id, customer_id=uuid4(), sale_amount=Decimal("5000000")
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.create_sale(uuid4(), uuid4(), data)
    assert exc_info.value.code == "CHECK_CONSTRAINT_VIOLATION"


@pytest.mark.asyncio
async def test_update_sale_rejects_already_inactive_sale() -> None:
    """Cancelling/reversing a sale that is no longer 'active' raises ConflictError."""
    service, _ = _service_with_mocked_deps()
    service.repository.get_by_id = AsyncMock(
        return_value={"id": uuid4(), "sale_status": "cancelled"}
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.update_sale(uuid4(), uuid4(), PropertySaleUpdate(sale_status="reversed"))
    assert exc_info.value.code == "SALE_NOT_ACTIVE"
