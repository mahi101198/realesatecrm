"""Repository for per-tenant Meta WhatsApp credentials
(whatsapp_tenant_configs). Encrypts on write, decrypts only in
get_decrypted -- get_public never touches the encrypted columns at all."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.whatsapp.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


class WhatsAppTenantConfigRepository:
    """Repository handling database access for whatsapp_tenant_configs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        waba_id: str,
        phone_number_id: str,
        verify_token: str,
        access_token_plain: str,
        app_secret_plain: str,
    ) -> dict[str, Any]:
        """Create or replace a tenant's WhatsApp config, encrypting both
        secrets before they ever reach the database."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.whatsapp_tenant_configs (
                    tenant_id, waba_id, phone_number_id, verify_token,
                    access_token_encrypted, app_secret_encrypted
                ) VALUES (
                    :tenant_id, :waba_id, :phone_number_id, :verify_token,
                    :access_token_encrypted, :app_secret_encrypted
                )
                ON CONFLICT (tenant_id) DO UPDATE SET
                    waba_id = EXCLUDED.waba_id,
                    phone_number_id = EXCLUDED.phone_number_id,
                    verify_token = EXCLUDED.verify_token,
                    access_token_encrypted = EXCLUDED.access_token_encrypted,
                    app_secret_encrypted = EXCLUDED.app_secret_encrypted,
                    is_active = true,
                    updated_at = NOW()
                RETURNING tenant_id, waba_id, phone_number_id, is_active,
                          created_at, updated_at
                """
            ),
            {
                "tenant_id": tenant_id,
                "waba_id": waba_id,
                "phone_number_id": phone_number_id,
                "verify_token": verify_token,
                "access_token_encrypted": encrypt_secret(access_token_plain),
                "app_secret_encrypted": encrypt_secret(app_secret_plain),
            },
        )
        return dict(result.mappings().one())

    async def get_public(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Fetch a tenant's WhatsApp config metadata -- never the secret
        columns, not even encrypted, since the admin GET endpoint must never
        return them."""
        result = await self.session.execute(
            text(
                """
                SELECT tenant_id, waba_id, phone_number_id, is_active,
                       created_at, updated_at
                FROM public.whatsapp_tenant_configs
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_decrypted(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Fetch a tenant's full WhatsApp config with both secrets
        decrypted to plaintext, for internal use only (client factory,
        webhook signature verification). Returns None if the tenant has no
        config row -- callers turn this into a 404, not a 500."""
        result = await self.session.execute(
            text(
                """
                SELECT tenant_id, waba_id, phone_number_id, verify_token,
                       access_token_encrypted, app_secret_encrypted, is_active
                FROM public.whatsapp_tenant_configs
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        if not row:
            return None
        data = dict(row)
        data["access_token"] = decrypt_secret(data.pop("access_token_encrypted"))
        data["app_secret"] = decrypt_secret(data.pop("app_secret_encrypted"))
        return data
