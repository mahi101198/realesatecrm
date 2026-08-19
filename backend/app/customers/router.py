"""Customer Domain REST API Router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.customers.schemas import (
    CustomerCreate,
    CustomerFilter,
    CustomerResponse,
    CustomerUpdate,
)
from app.customers.service import CustomerService
from app.db.session import get_db_session
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Customer",
    description="Create a new customer record within the authenticated tenant context.",
)
async def create_customer(
    data: CustomerCreate,
    context: RequestContext = Depends(require_permission(Permission.CUSTOMER_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> CustomerResponse:
    """Create customer endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a customer.")
    service = CustomerService(session)
    return await service.create_customer(tenant_id, data)


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Customer Details",
    description="Fetch details of a single customer by ID.",
)
async def get_customer(
    customer_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.CUSTOMER_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> CustomerResponse:
    """Get customer details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = CustomerService(session)
    customer = await service.get_customer(tenant_id, customer_id)
    ensure_tenant_resource_access(context, customer.tenant_id)
    return customer


@router.get(
    "",
    response_model=PaginatedResponse[CustomerResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Customers",
    description="Search and list customers with filtering and pagination.",
)
async def list_customers(
    query: Annotated[str | None, Query(description="Search name, phone, or email")] = None,
    phone: Annotated[str | None, Query(description="Filter by exact phone")] = None,
    email: Annotated[str | None, Query(description="Filter by exact email")] = None,
    city: Annotated[str | None, Query(description="Filter by city")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.CUSTOMER_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[CustomerResponse]:
    """List customers endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = CustomerFilter(query=query, phone=phone, email=email, city=city)
    pagination = PaginationParams(page=page, page_size=page_size)
    service = CustomerService(session)
    return await service.list_customers(tenant_id, filters, pagination)


@router.patch(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Customer",
    description="Update an existing customer record.",
)
async def update_customer(
    customer_id: UUID,
    data: CustomerUpdate,
    context: RequestContext = Depends(require_permission(Permission.CUSTOMER_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> CustomerResponse:
    """Update customer endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = CustomerService(session)
    customer = await service.get_customer(tenant_id, customer_id)
    ensure_tenant_resource_access(context, customer.tenant_id)
    return await service.update_customer(tenant_id, customer_id, data)
