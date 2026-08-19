"""Property Sale & Sale Payment REST API Router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.exceptions import NotFoundError
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.sales.schemas import (
    PropertySaleBalanceResponse,
    PropertySaleCreate,
    PropertySaleFilter,
    PropertySalePaymentCreate,
    PropertySalePaymentFilter,
    PropertySalePaymentResponse,
    PropertySalePaymentUpdate,
    PropertySaleResponse,
    PropertySaleUpdate,
)
from app.sales.service import PropertySalePaymentService, PropertySaleService
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/property-sales", tags=["Property Sales"])


@router.post(
    "",
    response_model=PropertySaleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Property Sale",
    description=(
        "Record a completed property sale. This is the moment ownership transfers -- "
        "atomically flips the property to 'sold', converts the booking (if referenced), "
        "marks the originating lead converted, and creates the new ownership record."
    ),
)
async def create_sale(
    data: PropertySaleCreate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_SALE_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertySaleResponse:
    """Create property sale endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a property sale.")
    service = PropertySaleService(session)
    return await service.create_sale(tenant_id, context.user_id, data)


@router.get(
    "/{sale_id}",
    response_model=PropertySaleResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Property Sale Details",
    description="Fetch details of a single property sale by ID.",
)
async def get_sale(
    sale_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_SALE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertySaleResponse:
    """Get property sale details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertySaleService(session)
    sale = await service.get_sale(tenant_id, sale_id)
    ensure_tenant_resource_access(context, sale.tenant_id)
    return sale


@router.get(
    "",
    response_model=PaginatedResponse[PropertySaleResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Property Sales",
    description="List and filter property sales with pagination.",
)
async def list_sales(
    property_id: Annotated[UUID | None, Query(description="Filter by property ID")] = None,
    customer_id: Annotated[UUID | None, Query(description="Filter by customer ID")] = None,
    sale_status: Annotated[
        str | None, Query(alias="status", description="Filter by sale status")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_SALE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[PropertySaleResponse]:
    """List property sales endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = PropertySaleFilter(
        property_id=property_id, customer_id=customer_id, sale_status=sale_status
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = PropertySaleService(session)
    return await service.list_sales(tenant_id, filters, pagination)


@router.patch(
    "/{sale_id}",
    response_model=PropertySaleResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel / Reverse Property Sale",
    description="Cancel or reverse an active property sale via sale_status.",
)
async def update_sale(
    sale_id: UUID,
    data: PropertySaleUpdate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_SALE_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertySaleResponse:
    """Update (cancel/reverse) property sale endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertySaleService(session)
    sale = await service.get_sale(tenant_id, sale_id)
    ensure_tenant_resource_access(context, sale.tenant_id)
    return await service.update_sale(tenant_id, sale_id, data)


@router.get(
    "/{sale_id}/balance",
    response_model=PropertySaleBalanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Sale Outstanding Balance",
    description="Outstanding balance rollup (sale_amount - discount - received payments).",
)
async def get_sale_balance(
    sale_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_SALE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertySaleBalanceResponse:
    """Get sale balance endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = PropertySaleService(session)
    sale = await service.get_sale(tenant_id, sale_id)
    ensure_tenant_resource_access(context, sale.tenant_id)
    return await service.get_balance(tenant_id, sale_id)


@router.post(
    "/{sale_id}/payments",
    response_model=PropertySalePaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record Sale Payment",
    description="Record a payment/installment against a sale.",
)
async def create_payment(
    sale_id: UUID,
    data: PropertySalePaymentCreate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_SALE_PAYMENT_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertySalePaymentResponse:
    """Create sale payment endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    sale_service = PropertySaleService(session)
    sale = await sale_service.get_sale(tenant_id, sale_id)
    ensure_tenant_resource_access(context, sale.tenant_id)

    service = PropertySalePaymentService(session)
    return await service.create_payment(tenant_id, sale_id, context.user_id, data)


@router.get(
    "/{sale_id}/payments",
    response_model=list[PropertySalePaymentResponse],
    status_code=status.HTTP_200_OK,
    summary="List Sale Payments",
    description="List payments/installments recorded against a sale.",
)
async def list_payments(
    sale_id: UUID,
    payment_status: Annotated[
        str | None, Query(alias="status", description="Filter by payment status")
    ] = None,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_SALE_PAYMENT_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[PropertySalePaymentResponse]:
    """List sale payments endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    sale_service = PropertySaleService(session)
    sale = await sale_service.get_sale(tenant_id, sale_id)
    ensure_tenant_resource_access(context, sale.tenant_id)

    service = PropertySalePaymentService(session)
    filters = PropertySalePaymentFilter(payment_status=payment_status)
    return await service.list_payments(tenant_id, sale_id, filters)


@router.patch(
    "/{sale_id}/payments/{payment_id}",
    response_model=PropertySalePaymentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Sale Payment",
    description="Correct a payment's status (e.g. bounced/refunded) or reference number.",
)
async def update_payment(
    sale_id: UUID,
    payment_id: UUID,
    data: PropertySalePaymentUpdate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_SALE_PAYMENT_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertySalePaymentResponse:
    """Update sale payment endpoint."""
    tenant_id = resolve_tenant_scope(context)
    sale_service = PropertySaleService(session)
    sale = await sale_service.get_sale(tenant_id, sale_id)
    ensure_tenant_resource_access(context, sale.tenant_id)

    service = PropertySalePaymentService(session)
    payment = await service.get_payment(tenant_id, payment_id)
    if payment.sale_id != sale_id:
        # Payment exists but belongs to a different sale than the URL claims --
        # treat as not-found rather than leaking cross-sale existence.
        raise NotFoundError(
            message=f"Payment with ID '{payment_id}' was not found.",
            code="PAYMENT_NOT_FOUND",
        )
    return await service.update_payment(tenant_id, payment_id, data)
