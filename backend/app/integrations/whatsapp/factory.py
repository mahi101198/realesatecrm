"""Factory building a per-tenant MetaWhatsAppClient from that tenant's
stored (encrypted) credentials. Keeps DB lookups and decryption out of
callers (app/whatsapp/service.py, app/webhooks/whatsapp/service.py)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.integrations.whatsapp.client import MetaWhatsAppClient
from app.integrations.whatsapp.repository import WhatsAppTenantConfigRepository


async def get_client_for_tenant(session: AsyncSession, tenant_id: UUID) -> MetaWhatsAppClient:
    """Build a MetaWhatsAppClient for the given tenant. Raises NotFoundError
    (WHATSAPP_NOT_CONFIGURED) if the tenant has no active config, so callers
    can turn this into a clean 404 rather than a 500."""
    repo = WhatsAppTenantConfigRepository(session)
    config = await repo.get_decrypted(tenant_id)
    if not config or not config["is_active"]:
        raise NotFoundError(
            message=f"Tenant '{tenant_id}' has no active WhatsApp configuration.",
            code="WHATSAPP_NOT_CONFIGURED",
        )
    return MetaWhatsAppClient(
        phone_number_id=config["phone_number_id"],
        access_token=config["access_token"],
        waba_id=config["waba_id"],
    )
