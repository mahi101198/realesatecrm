import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.customers.schemas import CustomerCreate, CustomerFilter, CustomerUpdate
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class CustomerRepository:
    """Repository handling database access for Customer entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, tenant_id: UUID, data: CustomerCreate) -> dict[str, Any]:
        """Insert a new customer record into PostgreSQL."""
        query = text(
            """
            INSERT INTO public.customers (
                tenant_id, full_name, first_name, last_name, phone,
                alternate_phone, email, gender, date_of_birth,
                address_line1, city, state, country, pincode,
                preferred_language, preferred_contact_time, do_not_call,
                do_not_whatsapp, do_not_email, whatsapp_opted_in,
                lead_source_id, referred_by, metadata
            ) VALUES (
                :tenant_id, :full_name, :first_name, :last_name, :phone,
                :alternate_phone, :email, :gender, :date_of_birth,
                :address_line1, :city, :state, :country, :pincode,
                :preferred_language, :preferred_contact_time, :do_not_call,
                :do_not_whatsapp, :do_not_email, :whatsapp_opted_in,
                :lead_source_id, :referred_by, CAST(:metadata AS jsonb)
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "full_name": data.full_name,
            "first_name": data.first_name,
            "last_name": data.last_name,
            "phone": data.phone,
            "alternate_phone": data.alternate_phone,
            "email": data.email,
            "gender": data.gender,
            "date_of_birth": data.date_of_birth,
            "address_line1": data.address_line1,
            "city": data.city,
            "state": data.state,
            "country": data.country,
            "pincode": data.pincode,
            "preferred_language": data.preferred_language,
            "preferred_contact_time": data.preferred_contact_time,
            "do_not_call": data.do_not_call,
            "do_not_whatsapp": data.do_not_whatsapp,
            "do_not_email": data.do_not_email,
            "whatsapp_opted_in": data.whatsapp_opted_in,
            "lead_source_id": data.lead_source_id,
            "referred_by": data.referred_by,
            "metadata": json.dumps(data.metadata or {}, default=str),
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def get_by_id(self, tenant_id: UUID | None, customer_id: UUID) -> dict[str, Any] | None:
        """Fetch customer by ID with tenant scope protection."""
        if tenant_id is not None:
            query = text(
                """
                SELECT * FROM public.customers
                WHERE id = :customer_id AND tenant_id = :tenant_id AND deleted_at IS NULL
                """
            )
            params = {"customer_id": customer_id, "tenant_id": tenant_id}
        else:
            query = text(
                """
                SELECT * FROM public.customers
                WHERE id = :customer_id AND deleted_at IS NULL
                """
            )
            params = {"customer_id": customer_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_by_phone(self, tenant_id: UUID, phone: str) -> dict[str, Any] | None:
        """Fetch non-deleted customer by phone within tenant."""
        query = text(
            """
            SELECT * FROM public.customers
            WHERE tenant_id = :tenant_id AND phone = :phone AND deleted_at IS NULL
            """
        )
        result = await self.session.execute(query, {"tenant_id": tenant_id, "phone": phone})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update(
        self, tenant_id: UUID | None, customer_id: UUID, data: CustomerUpdate
    ) -> dict[str, Any] | None:
        """Update existing customer record."""
        update_fields: dict[str, Any] = {}
        data_dict = data.model_dump(exclude_unset=True)

        for key, value in data_dict.items():
            if value is not None:
                if key == "metadata":
                    value = json.dumps(value or {}, default=str)
                update_fields[key] = value

        if not update_fields:
            return await self.get_by_id(tenant_id, customer_id)

        set_clauses = [
            f"{key} = CAST(:{key} AS jsonb)" if key == "metadata" else f"{key} = :{key}"
            for key in update_fields
        ]
        set_clause_str = ", ".join(set_clauses)

        if tenant_id is not None:
            query = text(
                f"""
                UPDATE public.customers
                SET {set_clause_str}, updated_at = NOW()
                WHERE id = :customer_id AND tenant_id = :tenant_id AND deleted_at IS NULL
                RETURNING *
                """  # noqa: S608
            )
            params = {**update_fields, "customer_id": customer_id, "tenant_id": tenant_id}
        else:
            query = text(
                f"""
                UPDATE public.customers
                SET {set_clause_str}, updated_at = NOW()
                WHERE id = :customer_id AND deleted_at IS NULL
                RETURNING *
                """  # noqa: S608
            )
            params = {**update_fields, "customer_id": customer_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def search(
        self,
        tenant_id: UUID | None,
        filters: CustomerFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search/list customers with pagination and filtering."""
        where_conditions = ["deleted_at IS NULL"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.query:
            where_conditions.append(
                "(full_name ILIKE :search_query OR "
                "phone ILIKE :search_query OR "
                "email ILIKE :search_query)"
            )
            params["search_query"] = f"%{filters.query.strip()}%"

        if filters.phone:
            where_conditions.append("phone = :filter_phone")
            params["filter_phone"] = filters.phone

        if filters.email:
            where_conditions.append("email = :filter_email")
            params["filter_email"] = filters.email

        if filters.city:
            where_conditions.append("city ILIKE :filter_city")
            params["filter_city"] = f"%{filters.city.strip()}%"

        where_str = " AND ".join(where_conditions)

        count_query = text(f"SELECT COUNT(*) FROM public.customers WHERE {where_str}")  # noqa: S608
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT * FROM public.customers
            WHERE {where_str}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count
