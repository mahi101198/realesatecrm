"""Unit tests for Property Ownership / Co-Owner / Resale Listing business rules."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.ownerships.schemas import (
    PropertyOwnershipCoOwnerCreate,
    PropertyOwnershipUpdate,
    PropertyResaleListingCreate,
    PropertyResaleListingUpdate,
)
from app.ownerships.service import PropertyOwnershipService, PropertyResaleListingService


def _service_with_customer_check_ok() -> tuple[PropertyOwnershipService, AsyncMock]:
    session = AsyncMock()
    check = MagicMock()
    check.scalar_one_or_none.return_value = uuid4()
    session.execute.return_value = check
    return PropertyOwnershipService(session), session


@pytest.mark.asyncio
async def test_get_current_raises_when_no_active_owner() -> None:
    """Verify GET current owner raises NotFoundError when property has never been sold."""
    service, _ = _service_with_customer_check_ok()
    service.repository.get_current_for_property = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError) as exc_info:
        await service.get_current(uuid4(), uuid4())
    assert exc_info.value.code == "NO_CURRENT_OWNER"


@pytest.mark.asyncio
async def test_update_ownership_rejects_non_reversed_status() -> None:
    """Verify PATCH ownership rejects any ownership_status other than 'reversed'."""
    service, _ = _service_with_customer_check_ok()
    service.repository.get_by_id = AsyncMock(
        return_value={"id": uuid4(), "ownership_status": "active"}
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_ownership(
            uuid4(), uuid4(), PropertyOwnershipUpdate(ownership_status="active")
        )
    assert exc_info.value.code == "INVALID_OWNERSHIP_STATUS_TRANSITION"


@pytest.mark.asyncio
async def test_update_ownership_rejects_double_reversal() -> None:
    """Verify reversing an already-reversed ownership record raises ConflictError."""
    service, _ = _service_with_customer_check_ok()
    service.repository.get_by_id = AsyncMock(
        return_value={"id": uuid4(), "ownership_status": "reversed"}
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.update_ownership(
            uuid4(), uuid4(), PropertyOwnershipUpdate(ownership_status="reversed")
        )
    assert exc_info.value.code == "OWNERSHIP_ALREADY_REVERSED"


@pytest.mark.asyncio
async def test_update_ownership_maps_bad_verified_by_fk_to_validation_error() -> None:
    """Verify PATCHing verified_by with a malformed/non-existent user UUID
    (a FK violation, since verified_by is not pre-checked against users)
    raises a clean ValidationError instead of a raw 500."""
    service, _ = _service_with_customer_check_ok()
    ownership_id = uuid4()
    service.repository.get_by_id = AsyncMock(
        return_value={"id": ownership_id, "ownership_status": "active"}
    )
    service.repository.update = AsyncMock(
        side_effect=DBAPIError(
            "update",
            {},
            Exception(
                'insert or update on table "property_ownerships" violates foreign '
                'key constraint "property_ownerships_verified_by_fkey"'
            ),
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_ownership(
            uuid4(), ownership_id, PropertyOwnershipUpdate(verified_by=uuid4())
        )
    assert exc_info.value.code == "INVALID_REFERENCE"


@pytest.mark.asyncio
async def test_add_co_owner_maps_db_trigger_exception_to_conflict_error() -> None:
    """Verify the DB's trg_validate_co_owner_share (errcode P0004) exception is
    surfaced as a clean ConflictError, not a raw DBAPIError leaking to the client."""
    service, _ = _service_with_customer_check_ok()
    ownership_id = uuid4()
    service.repository.get_by_id = AsyncMock(return_value={"id": ownership_id})
    service.repository.add_co_owner = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception("total share_percentage ... exceeds 100 (P0004)"),
        )
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.add_co_owner(
            uuid4(), ownership_id, PropertyOwnershipCoOwnerCreate(customer_id=uuid4())
        )
    assert exc_info.value.code == "CO_OWNER_SHARE_EXCEEDS_100"


@pytest.mark.asyncio
async def test_add_co_owner_maps_trigger_exception_via_sqlstate_attribute() -> None:
    """Verify the P0004 trigger exception is also recognized via the driver
    exception's .sqlstate attribute (the more robust/primary check), not only
    via a text match against the message -- asyncpg surfaces custom
    RAISE EXCEPTION ... USING ERRCODE as a PostgresError with .sqlstate set."""
    service, _ = _service_with_customer_check_ok()
    ownership_id = uuid4()
    service.repository.get_by_id = AsyncMock(return_value={"id": ownership_id})

    orig = Exception("total share_percentage for ownership_id ... would be 130")
    orig.sqlstate = "P0004"  # type: ignore[attr-defined]
    service.repository.add_co_owner = AsyncMock(side_effect=DBAPIError("insert", {}, orig))

    with pytest.raises(ConflictError) as exc_info:
        await service.add_co_owner(
            uuid4(), ownership_id, PropertyOwnershipCoOwnerCreate(customer_id=uuid4())
        )
    assert exc_info.value.code == "CO_OWNER_SHARE_EXCEEDS_100"


@pytest.mark.asyncio
async def test_add_co_owner_maps_unique_violation_to_duplicate_co_owner() -> None:
    """Verify adding the SAME customer twice as a co-owner (violating
    uq_co_owners_ownership_customer) maps specifically to DUPLICATE_CO_OWNER,
    not the generic share-exceeded or fallback path."""
    service, _ = _service_with_customer_check_ok()
    ownership_id = uuid4()
    service.repository.get_by_id = AsyncMock(return_value={"id": ownership_id})
    service.repository.add_co_owner = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception(
                'duplicate key value violates unique constraint "uq_co_owners_ownership_customer"'
            ),
        )
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.add_co_owner(
            uuid4(), ownership_id, PropertyOwnershipCoOwnerCreate(customer_id=uuid4())
        )
    assert exc_info.value.code == "DUPLICATE_CO_OWNER"


@pytest.mark.asyncio
async def test_add_co_owner_falls_back_to_shared_helper_for_other_db_errors() -> None:
    """Verify a DB error that is neither the share-trigger nor the unique
    co-owner constraint (e.g. a stale/cross-tenant customer_id slipping past
    the pre-check and hitting a FK violation) is handled by the shared
    db_errors helper rather than being mislabeled as a duplicate."""
    service, _ = _service_with_customer_check_ok()
    ownership_id = uuid4()
    service.repository.get_by_id = AsyncMock(return_value={"id": ownership_id})
    service.repository.add_co_owner = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception(
                'insert or update on table "property_ownership_co_owners" '
                'violates foreign key constraint "fk_co_owners_customer_tenant"'
            ),
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.add_co_owner(
            uuid4(), ownership_id, PropertyOwnershipCoOwnerCreate(customer_id=uuid4())
        )
    assert exc_info.value.code == "INVALID_REFERENCE"


@pytest.mark.asyncio
async def test_add_co_owner_raises_not_found_for_unknown_ownership() -> None:
    """Verify adding a co-owner to a non-existent ownership record raises NotFoundError."""
    service, _ = _service_with_customer_check_ok()
    service.repository.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError):
        await service.add_co_owner(
            uuid4(), uuid4(), PropertyOwnershipCoOwnerCreate(customer_id=uuid4())
        )


@pytest.mark.asyncio
async def test_create_resale_listing_rejects_non_current_ownership() -> None:
    """Verify a resale listing can only be created against the currently-active
    ownership record (ownership_end_date IS NULL)."""
    session = AsyncMock()
    service = PropertyResaleListingService(session)
    service.ownership_repository.get_by_id = AsyncMock(
        return_value={"id": uuid4(), "ownership_end_date": "2025-01-01"}
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.create_listing(
            uuid4(), uuid4(), PropertyResaleListingCreate(ownership_id=uuid4())
        )
    assert exc_info.value.code == "OWNERSHIP_NOT_CURRENT"


@pytest.mark.asyncio
async def test_create_resale_listing_rejects_duplicate_open_listing() -> None:
    """Verify the DB's uq_property_resale_listings_one_open_per_ownership index
    violation is mapped to a clean ConflictError."""
    session = AsyncMock()
    service = PropertyResaleListingService(session)
    ownership_id = uuid4()
    service.ownership_repository.get_by_id = AsyncMock(
        return_value={"id": ownership_id, "ownership_end_date": None}
    )
    service.repository.create = AsyncMock(
        side_effect=IntegrityError("insert", {}, Exception("duplicate key"))
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.create_listing(
            uuid4(),
            uuid4(),
            PropertyResaleListingCreate(ownership_id=ownership_id, asking_price=Decimal("7500000")),
        )
    assert exc_info.value.code == "DUPLICATE_OPEN_RESALE_LISTING"


@pytest.mark.asyncio
async def test_create_resale_listing_falls_back_to_shared_helper_for_other_db_errors() -> None:
    """Verify a resale-listing create failure that is NOT the duplicate-open-
    listing constraint (e.g. a malformed asking_price that somehow reached the
    DB, surfacing as a CHECK violation) is handled by the shared helper."""
    session = AsyncMock()
    service = PropertyResaleListingService(session)
    ownership_id = uuid4()
    service.ownership_repository.get_by_id = AsyncMock(
        return_value={"id": ownership_id, "ownership_end_date": None}
    )
    service.repository.create = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception(
                'new row for relation "property_resale_listings" violates check '
                'constraint "chk_property_resale_listings_asking_price"'
            ),
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.create_listing(
            uuid4(), uuid4(), PropertyResaleListingCreate(ownership_id=ownership_id)
        )
    assert exc_info.value.code == "CHECK_CONSTRAINT_VIOLATION"


@pytest.mark.asyncio
async def test_update_resale_listing_maps_db_error_via_shared_helper() -> None:
    """Verify a DB error during a resale-listing PATCH (e.g. a malformed
    asking_price CHECK violation) maps to a clean ValidationError."""
    session = AsyncMock()
    service = PropertyResaleListingService(session)
    listing_id = uuid4()
    service.repository.get_by_id = AsyncMock(
        return_value={"id": listing_id, "listing_status": "open"}
    )
    service.repository.update = AsyncMock(
        side_effect=DBAPIError(
            "update",
            {},
            Exception(
                'new row for relation "property_resale_listings" violates check '
                'constraint "chk_property_resale_listings_asking_price"'
            ),
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_listing(
            uuid4(), listing_id, PropertyResaleListingUpdate(asking_price=Decimal("5000000"))
        )
    assert exc_info.value.code == "CHECK_CONSTRAINT_VIOLATION"


@pytest.mark.asyncio
async def test_update_resale_listing_rejects_converted_status() -> None:
    """Verify PATCH resale listing cannot set listing_status='converted' directly."""
    session = AsyncMock()
    service = PropertyResaleListingService(session)
    service.repository.get_by_id = AsyncMock(return_value={"id": uuid4(), "listing_status": "open"})

    with pytest.raises(ValidationError) as exc_info:
        await service.update_listing(
            uuid4(), uuid4(), PropertyResaleListingUpdate(listing_status="converted")
        )
    assert exc_info.value.code == "INVALID_LISTING_STATUS_TRANSITION"


@pytest.mark.asyncio
async def test_update_resale_listing_rejects_withdrawing_non_open_listing() -> None:
    """Verify withdrawing a listing that is not 'open' raises ConflictError."""
    session = AsyncMock()
    service = PropertyResaleListingService(session)
    service.repository.get_by_id = AsyncMock(
        return_value={"id": uuid4(), "listing_status": "converted"}
    )

    with pytest.raises(ConflictError) as exc_info:
        await service.update_listing(
            uuid4(), uuid4(), PropertyResaleListingUpdate(listing_status="withdrawn")
        )
    assert exc_info.value.code == "LISTING_NOT_OPEN"
