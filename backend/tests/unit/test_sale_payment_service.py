"""Unit tests for PropertySalePaymentService: business rules and DB-level
failure hardening (invalid payment_mode/payment_status enum values, and
NotFoundError paths) for payments recorded against a property sale."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import NotFoundError, ValidationError
from app.sales.schemas import PropertySalePaymentCreate, PropertySalePaymentUpdate
from app.sales.service import PropertySalePaymentService


def _service() -> PropertySalePaymentService:
    session = AsyncMock()
    return PropertySalePaymentService(session)


@pytest.mark.asyncio
async def test_create_payment_raises_not_found_for_unknown_sale() -> None:
    """Verify recording a payment against a non-existent/cross-tenant sale
    raises NotFoundError rather than proceeding."""
    service = _service()
    service.sale_repository.get_by_id = AsyncMock(return_value=None)

    data = PropertySalePaymentCreate(amount=Decimal("100000"), payment_mode="cash")
    with pytest.raises(NotFoundError) as exc_info:
        await service.create_payment(uuid4(), uuid4(), uuid4(), data)
    assert exc_info.value.code == "PROPERTY_SALE_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_payment_maps_invalid_payment_mode_to_validation_error() -> None:
    """Verify an invalid payment_mode enum value (not caught by Pydantic,
    which only requires a non-empty string) maps to a clean ValidationError
    instead of a raw 500 when the DB rejects the enum cast."""
    service = _service()
    sale_id = uuid4()
    service.sale_repository.get_by_id = AsyncMock(return_value={"id": sale_id})
    service.repository.create = AsyncMock(
        side_effect=DBAPIError(
            "insert", {}, Exception('invalid input value for enum public.payment_mode: "bogus"')
        )
    )

    data = PropertySalePaymentCreate(amount=Decimal("100000"), payment_mode="bogus")
    with pytest.raises(ValidationError) as exc_info:
        await service.create_payment(uuid4(), sale_id, uuid4(), data)
    assert exc_info.value.code == "INVALID_FIELD_VALUE"


@pytest.mark.asyncio
async def test_update_payment_raises_not_found_for_unknown_payment() -> None:
    """Verify updating a non-existent payment raises NotFoundError."""
    service = _service()
    service.repository.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError) as exc_info:
        await service.update_payment(
            uuid4(), uuid4(), PropertySalePaymentUpdate(payment_status="bounced")
        )
    assert exc_info.value.code == "PAYMENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_payment_maps_invalid_payment_status_to_validation_error() -> None:
    """Verify an invalid payment_status enum value maps to a clean
    ValidationError rather than a raw 500."""
    service = _service()
    payment_id = uuid4()
    service.repository.get_by_id = AsyncMock(return_value={"id": payment_id})
    service.repository.update = AsyncMock(
        side_effect=DBAPIError(
            "update", {}, Exception('invalid input value for enum public.payment_status: "bogus"')
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_payment(
            uuid4(), payment_id, PropertySalePaymentUpdate(payment_status="bogus")
        )
    assert exc_info.value.code == "INVALID_FIELD_VALUE"


@pytest.mark.asyncio
async def test_list_payments_raises_not_found_for_unknown_sale() -> None:
    """Verify listing payments for a non-existent sale raises NotFoundError."""
    service = _service()
    service.sale_repository.get_by_id = AsyncMock(return_value=None)

    from app.sales.schemas import PropertySalePaymentFilter

    with pytest.raises(NotFoundError) as exc_info:
        await service.list_payments(uuid4(), uuid4(), PropertySalePaymentFilter())
    assert exc_info.value.code == "PROPERTY_SALE_NOT_FOUND"
