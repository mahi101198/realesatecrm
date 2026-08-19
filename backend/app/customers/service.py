"""Customer Business Service Layer."""

import logging
from math import ceil
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.customers.repository import CustomerRepository
from app.customers.schemas import (
    CustomerCreate,
    CustomerFilter,
    CustomerResponse,
    CustomerUpdate,
)
from app.shared.schemas import PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)


class CustomerService:
    """Service handling business rules and workflows for Customers."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = CustomerRepository(session)

    async def create_customer(self, tenant_id: UUID, data: CustomerCreate) -> CustomerResponse:
        """Create a new customer within a tenant after duplicate phone check."""
        existing = await self.repository.get_by_phone(tenant_id, data.phone)
        if existing:
            raise ConflictError(
                message=f"A customer with phone number '{data.phone}' already exists.",
                code="DUPLICATE_CUSTOMER_PHONE",
            )

        row = await self.repository.create(tenant_id, data)
        return CustomerResponse.model_validate(row)

    async def get_customer(self, tenant_id: UUID | None, customer_id: UUID) -> CustomerResponse:
        """Get customer by ID with tenant security check."""
        row = await self.repository.get_by_id(tenant_id, customer_id)
        if not row:
            raise NotFoundError(
                message=f"Customer with ID '{customer_id}' was not found.",
                code="CUSTOMER_NOT_FOUND",
            )
        return CustomerResponse.model_validate(row)

    async def update_customer(
        self, tenant_id: UUID | None, customer_id: UUID, data: CustomerUpdate
    ) -> CustomerResponse:
        """Update customer details."""
        existing = await self.repository.get_by_id(tenant_id, customer_id)
        if not existing:
            raise NotFoundError(
                message=f"Customer with ID '{customer_id}' was not found.",
                code="CUSTOMER_NOT_FOUND",
            )

        if data.phone and data.phone != existing["phone"]:
            target_tenant = tenant_id or existing["tenant_id"]
            duplicate = await self.repository.get_by_phone(target_tenant, data.phone)
            if duplicate and duplicate["id"] != customer_id:
                raise ConflictError(
                    message=f"A customer with phone number '{data.phone}' already exists.",
                    code="DUPLICATE_CUSTOMER_PHONE",
                )

        updated_row = await self.repository.update(tenant_id, customer_id, data)
        if not updated_row:
            raise NotFoundError(
                message=f"Customer with ID '{customer_id}' was not found.",
                code="CUSTOMER_NOT_FOUND",
            )
        return CustomerResponse.model_validate(updated_row)

    async def list_customers(
        self,
        tenant_id: UUID | None,
        filters: CustomerFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[CustomerResponse]:
        """Search and list customers with pagination."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [CustomerResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[CustomerResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )
