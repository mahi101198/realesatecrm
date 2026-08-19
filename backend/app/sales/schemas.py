"""Property Sale & Sale Payment Domain Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PropertySaleCreate(BaseModel):
    """Request payload to record a completed property sale.

    Creating this row is the moment ownership transfers -- see
    PropertySaleService.create_sale for the full atomic transaction.
    """

    property_id: UUID = Field(..., description="Property being sold")
    customer_id: UUID = Field(..., description="Buyer")
    booking_id: UUID | None = Field(
        default=None, description="Prior token booking this sale converts, if any"
    )
    sale_date: date | None = Field(default=None, description="Defaults to today")
    sale_amount: Decimal = Field(..., ge=0)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0)
    purchase_purpose: str | None = Field(
        default=None, description="investment / end_use / rental / resale / commercial_use / other"
    )


class PropertySaleUpdate(BaseModel):
    """Request payload to cancel or reverse a sale. sale_amount/dates are immutable
    once recorded -- corrections happen via sale_status, not editing the figures."""

    sale_status: str = Field(..., description="'cancelled' or 'reversed'")


class PropertySaleResponse(BaseModel):
    """Property sale API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    booking_id: UUID | None = None
    property_id: UUID
    customer_id: UUID
    sale_date: date
    sale_amount: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    sale_status: str
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class PropertySaleFilter(BaseModel):
    """Filter parameters for listing property sales."""

    property_id: UUID | None = None
    customer_id: UUID | None = None
    sale_status: str | None = None


class PropertySaleBalanceResponse(BaseModel):
    """Outstanding balance rollup, sourced from v_property_sale_balances."""

    model_config = ConfigDict(from_attributes=True)

    sale_id: UUID
    tenant_id: UUID
    property_id: UUID
    customer_id: UUID
    sale_amount: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    amount_received: Decimal
    outstanding_balance: Decimal


class PropertySalePaymentCreate(BaseModel):
    """Request payload to record a payment/installment against a sale."""

    payment_date: date | None = Field(default=None, description="Defaults to today")
    amount: Decimal = Field(..., ge=0)
    payment_mode: str = Field(..., description="cash/cheque/bank_transfer/upi/card/other")
    payment_status: str = Field(
        default="received", description="pending/received/bounced/refunded"
    )
    reference_number: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class PropertySalePaymentUpdate(BaseModel):
    """Request payload to correct a payment's status (e.g. bounced/refunded)."""

    payment_status: str | None = None
    reference_number: str | None = None
    notes: str | None = None


class PropertySalePaymentResponse(BaseModel):
    """Property sale payment API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    sale_id: UUID
    payment_date: date
    amount: Decimal
    payment_mode: str
    payment_status: str
    reference_number: str | None = None
    notes: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class PropertySalePaymentFilter(BaseModel):
    """Filter parameters for listing payments against a sale."""

    payment_status: str | None = None
