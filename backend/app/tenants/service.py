"""Tenant Business Service Layer."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.integrations.whatsapp.repository import WhatsAppTenantConfigRepository
from app.tenants.repository import TenantRepository
from app.tenants.schemas import (
    TenantResponse,
    TenantUpdate,
    WhatsAppConfigResponse,
    WhatsAppConfigUpsertRequest,
)

logger = logging.getLogger(__name__)

# Runtime guard mirroring TenantUpdate's docstring: these fields must never
# reach TenantRepository.update, because the DB-level protection for them
# (trg_fn_protect_tenant_plan_settings) explicitly bypasses service_role,
# which is how this backend always connects. This check is a second,
# belt-and-suspenders line of defense against a future schema edit that
# carelessly re-adds one of these fields to TenantUpdate without noticing
# why they were excluded.
_FORBIDDEN_TENANT_FIELDS = frozenset(
    {
        "slug",
        "plan",
        "plan_expires_at",
        "max_users",
        "max_properties",
        "max_ai_minutes",
        "is_active",
    }
)


class TenantService:
    """Service for tenant company-profile read/update."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = TenantRepository(session)

    async def get_tenant(self, tenant_id: UUID) -> TenantResponse:
        """Fetch a tenant by ID."""
        row = await self.repository.get_by_id(tenant_id)
        if not row:
            raise NotFoundError(
                message=f"Tenant with ID '{tenant_id}' was not found.", code="TENANT_NOT_FOUND"
            )
        return TenantResponse.model_validate(row)

    async def update_tenant(self, tenant_id: UUID, data: TenantUpdate) -> TenantResponse:
        """Update tenant company-profile fields."""
        existing = await self.repository.get_by_id(tenant_id)
        if not existing:
            raise NotFoundError(
                message=f"Tenant with ID '{tenant_id}' was not found.", code="TENANT_NOT_FOUND"
            )

        data_dict = data.model_dump(exclude_unset=True)
        leaked = _FORBIDDEN_TENANT_FIELDS & data_dict.keys()
        if leaked:
            logger.error(f"TenantUpdate carried forbidden subscription field(s): {leaked}")
            raise ValidationError(
                message="Subscription/plan settings cannot be modified through this endpoint.",
                code="TENANT_PLAN_FIELD_FORBIDDEN",
            )

        updated_row = await self.repository.update(tenant_id, data)
        if not updated_row:
            raise NotFoundError(
                message=f"Tenant with ID '{tenant_id}' was not found.", code="TENANT_NOT_FOUND"
            )
        return TenantResponse.model_validate(updated_row)

    async def upsert_whatsapp_config(
        self, tenant_id: UUID, data: WhatsAppConfigUpsertRequest
    ) -> WhatsAppConfigResponse:
        """Create or rotate a tenant's Meta WhatsApp credentials."""
        existing = await self.repository.get_by_id(tenant_id)
        if not existing:
            raise NotFoundError(
                message=f"Tenant with ID '{tenant_id}' was not found.", code="TENANT_NOT_FOUND"
            )
        whatsapp_repo = WhatsAppTenantConfigRepository(self.repository.session)
        row = await whatsapp_repo.upsert(
            tenant_id=tenant_id,
            waba_id=data.waba_id,
            phone_number_id=data.phone_number_id,
            verify_token=data.verify_token,
            access_token_plain=data.access_token,
            app_secret_plain=data.app_secret,
        )
        return WhatsAppConfigResponse.model_validate(row)

    async def get_whatsapp_config(self, tenant_id: UUID) -> WhatsAppConfigResponse:
        """Fetch a tenant's WhatsApp config metadata (never the secrets)."""
        whatsapp_repo = WhatsAppTenantConfigRepository(self.repository.session)
        row = await whatsapp_repo.get_public(tenant_id)
        if not row:
            raise NotFoundError(
                message=f"Tenant '{tenant_id}' has no WhatsApp configuration.",
                code="WHATSAPP_NOT_CONFIGURED",
            )
        return WhatsAppConfigResponse.model_validate(row)
