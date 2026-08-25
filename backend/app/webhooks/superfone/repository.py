"""Repository for per-tenant Superfone CRM webhook bearer secrets
(superfone_crm_tenant_configs, see migration 033). Only ever stores/reads
the SHA-256 hash -- the plaintext secret never reaches this table."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.webhooks.superfone.security import hash_secret


class SuperfoneCrmTenantConfigRepository:
    """Repository handling database access for superfone_crm_tenant_configs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, tenant_id: UUID, secret_plain: str) -> dict[str, Any]:
        """Create or rotate a tenant's CRM webhook bearer secret, hashing it
        before it ever reaches the database."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.superfone_crm_tenant_configs (tenant_id, bearer_secret_hash)
                VALUES (:tenant_id, :bearer_secret_hash)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    bearer_secret_hash = EXCLUDED.bearer_secret_hash,
                    is_active = true,
                    updated_at = NOW()
                RETURNING tenant_id, is_active, created_at, updated_at
                """
            ),
            {"tenant_id": tenant_id, "bearer_secret_hash": hash_secret(secret_plain)},
        )
        return dict(result.mappings().one())

    async def get_public(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Fetch a tenant's config metadata -- never the secret hash, since
        the admin GET endpoint must never return anything derived from the
        credential."""
        result = await self.session.execute(
            text(
                """
                SELECT tenant_id, is_active, created_at, updated_at
                FROM public.superfone_crm_tenant_configs
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_secret_hash(self, tenant_id: UUID) -> str | None:
        """Fetch a tenant's stored bearer-secret hash, for internal use only
        (webhook authenticity check). Returns None if the tenant has no
        config row or it's inactive -- callers treat that identically to a
        wrong token, never distinguishing the two in the response."""
        result = await self.session.execute(
            text(
                """
                SELECT bearer_secret_hash
                FROM public.superfone_crm_tenant_configs
                WHERE tenant_id = :tenant_id AND is_active = true
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return row["bearer_secret_hash"] if row else None
