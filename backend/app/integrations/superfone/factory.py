"""Factory functions building configured Superfone clients from app settings.

Keeps SecretStr-unwrapping and base-URL wiring in one place so callers
(gateway.py, webhook router, sales-handoff acceptance) never touch
`settings.SUPERFONE_*` directly.
"""

from app.core.config import settings
from app.integrations.superfone.client import SFVoPIClient, SuperfoneCRMClient


def get_sfvopi_client() -> SFVoPIClient:
    """Build a SFVoPIClient from configured settings."""
    return SFVoPIClient(
        api_key=settings.SUPERFONE_SFVOPI_API_KEY.get_secret_value(),
        base_url=settings.SUPERFONE_SFVOPI_BASE_URL,
    )


def get_superfone_crm_client() -> SuperfoneCRMClient:
    """Build a SuperfoneCRMClient from configured settings."""
    return SuperfoneCRMClient(
        api_key=settings.SUPERFONE_CRM_API_KEY.get_secret_value(),
        base_url=settings.SUPERFONE_CRM_BASE_URL,
    )
