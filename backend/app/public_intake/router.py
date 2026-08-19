"""Public (Unauthenticated) Lead Intake REST API Router.

This is a deliberately different entry point from the staff-facing
POST /customers / POST /leads endpoints:

  - No RequestContext, no require_permission dependency, no JWT at all --
    this is meant to be called from a public website/campaign form.
  - Tenant is resolved from a server-controlled path segment (tenants.slug),
    never from a client-supplied tenant_id. See PublicIntakeService for the
    fail-closed resolution logic.
  - Redis-backed rate limiting protects this endpoint specifically, since it
    is the one write path in this codebase with no auth gate at all.

Kept in its own module/router rather than folded into app/leads/ or
app/customers/, since the auth model, identity-resolution flow, and abuse
surface are all fundamentally different from the staff-facing endpoints.
"""

import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.public_intake.rate_limit import enforce_public_intake_rate_limit
from app.public_intake.schemas import PublicLeadIntakeRequest, PublicLeadIntakeResponse
from app.public_intake.service import PublicIntakeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/intake", tags=["Public Lead Intake"])


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate-limit keying.

    Only trusts X-Forwarded-For when settings.TRUST_PROXY_HEADERS is True --
    that header is fully attacker-controlled on a direct connection, and this
    is the one endpoint where the rate limiter IS the abuse defense, so
    trusting it unconditionally would let any caller spoof a new IP per
    request and defeat the limiter entirely. Enabling TRUST_PROXY_HEADERS is
    only safe when this deployment's proxy/load-balancer layer is known to
    strip or overwrite any inbound client-supplied X-Forwarded-For before
    forwarding -- see the setting's docstring in app/core/config.py.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post(
    "/{tenant_slug}/leads",
    response_model=PublicLeadIntakeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Public Lead Enquiry Intake",
    description=(
        "Anonymous website/campaign enquiry intake. tenant_slug is a public, "
        "server-resolved identifier -- no tenant_id, permission, or auth token "
        "is accepted or required. Rate-limited per (tenant, client IP)."
    ),
)
async def submit_public_enquiry(
    tenant_slug: str,
    data: PublicLeadIntakeRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> PublicLeadIntakeResponse:
    """Public lead intake endpoint."""
    client_ip = _client_ip(request)
    await enforce_public_intake_rate_limit(tenant_slug, client_ip)

    service = PublicIntakeService(session)
    tenant_id = await service.resolve_tenant_id(tenant_slug)
    return await service.submit_enquiry(tenant_id, data)
