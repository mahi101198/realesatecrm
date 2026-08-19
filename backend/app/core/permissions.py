"""Centralized Permission Codes and Authorization Helpers.

Permission checks use trusted RequestContext built from the database.
Never read application role or permissions from the JWT.
"""

from enum import StrEnum
from uuid import UUID

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.request_context import RequestContext, SecurityScope


class Permission(StrEnum):
    """Granular permission codes as defined in database migrations 003 / 016 / 018."""

    # Customer
    CUSTOMER_READ = "customer.read"
    CUSTOMER_CREATE = "customer.create"
    CUSTOMER_UPDATE = "customer.update"
    CUSTOMER_DELETE = "customer.delete"

    # Lead
    LEAD_READ = "lead.read"
    LEAD_CREATE = "lead.create"
    LEAD_UPDATE = "lead.update"
    LEAD_ASSIGN = "lead.assign"
    LEAD_DELETE = "lead.delete"

    # Property
    PROPERTY_READ = "property.read"
    PROPERTY_CREATE = "property.create"
    PROPERTY_UPDATE = "property.update"
    PROPERTY_DELETE = "property.delete"

    # Project
    PROJECT_READ = "project.read"
    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"

    # Campaign
    CAMPAIGN_READ = "campaign.read"
    CAMPAIGN_CREATE = "campaign.create"
    CAMPAIGN_UPDATE = "campaign.update"
    CAMPAIGN_START = "campaign.start"
    CAMPAIGN_PAUSE = "campaign.pause"

    # Call
    CALL_READ = "call.read"
    CALL_RECORDING_READ = "call.recording.read"

    # Appointment
    APPOINTMENT_READ = "appointment.read"
    APPOINTMENT_CREATE = "appointment.create"
    APPOINTMENT_UPDATE = "appointment.update"
    APPOINTMENT_CANCEL = "appointment.cancel"

    # Agent Config
    AGENT_CONFIG_READ = "agent_config.read"
    AGENT_CONFIG_UPDATE = "agent_config.update"

    # Sales Agent
    SALES_AGENT_READ = "sales_agent.read"
    SALES_AGENT_ASSIGN = "sales_agent.assign"

    # Analytics
    ANALYTICS_READ = "analytics.read"

    # Audit
    AUDIT_READ = "audit.read"

    # User administration (migration 016)
    USER_READ = "user.read"
    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"

    # Location (migration 024)
    LOCATION_READ = "location.read"
    LOCATION_CREATE = "location.create"
    LOCATION_UPDATE = "location.update"

    # Property Booking (migration 023)
    PROPERTY_BOOKING_READ = "property_booking.read"
    PROPERTY_BOOKING_CREATE = "property_booking.create"
    PROPERTY_BOOKING_UPDATE = "property_booking.update"

    # Property Sale (migration 023)
    PROPERTY_SALE_READ = "property_sale.read"
    PROPERTY_SALE_CREATE = "property_sale.create"
    PROPERTY_SALE_UPDATE = "property_sale.update"

    # Property Ownership (migration 023)
    PROPERTY_OWNERSHIP_READ = "property_ownership.read"
    PROPERTY_OWNERSHIP_CREATE = "property_ownership.create"
    PROPERTY_OWNERSHIP_UPDATE = "property_ownership.update"

    # Construction Milestone (migration 025)
    CONSTRUCTION_MILESTONE_READ = "construction_milestone.read"
    CONSTRUCTION_MILESTONE_CREATE = "construction_milestone.create"
    CONSTRUCTION_MILESTONE_UPDATE = "construction_milestone.update"

    # Property Sale Payment (migration 025)
    PROPERTY_SALE_PAYMENT_READ = "property_sale_payment.read"
    PROPERTY_SALE_PAYMENT_CREATE = "property_sale_payment.create"
    PROPERTY_SALE_PAYMENT_UPDATE = "property_sale_payment.update"

    # Resale Listing (migration 025)
    RESALE_LISTING_READ = "resale_listing.read"
    RESALE_LISTING_CREATE = "resale_listing.create"
    RESALE_LISTING_UPDATE = "resale_listing.update"

    # WhatsApp Messaging (migration 028)
    WHATSAPP_MESSAGE_READ = "whatsapp_message.read"
    WHATSAPP_MESSAGE_CREATE = "whatsapp_message.create"
    WHATSAPP_TEMPLATE_READ = "whatsapp_template.read"

    # Platform (migration 018 — super_admin only in seed)
    PLATFORM_TENANT_READ = "platform.tenant.read"
    PLATFORM_TENANT_CREATE = "platform.tenant.create"
    PLATFORM_TENANT_UPDATE = "platform.tenant.update"
    PLATFORM_TENANT_SUSPEND = "platform.tenant.suspend"
    PLATFORM_USER_IMPERSONATE = "platform.user.impersonate"
    PLATFORM_BILLING_READ = "platform.billing.read"
    PLATFORM_ANALYTICS_READ = "platform.analytics.read"
    PLATFORM_WHATSAPP_CONFIG_MANAGE = "platform.whatsapp_config.manage"


def permission_code(permission: Permission | str) -> str:
    """Normalize Permission enum or raw string to a permission code."""
    return permission if isinstance(permission, str) else permission.value


def has_permission(context: RequestContext, permission: Permission | str) -> bool:
    """Return True if the request context includes the given permission code."""
    return permission_code(permission) in context.permissions


def check_permission(context: RequestContext, permission: Permission | str) -> None:
    """Raise ForbiddenError when the context lacks the required permission.

    Super admin does NOT bypass missing permissions — they must hold the
    permission in the database like every other role.
    """
    code = permission_code(permission)
    if code not in context.permissions:
        raise ForbiddenError(
            message="You do not have permission to perform this action.",
            code="INSUFFICIENT_PERMISSIONS",
        )


def check_super_admin(context: RequestContext) -> None:
    """Raise ForbiddenError unless context is a platform super_admin."""
    if not context.is_super_admin:
        raise ForbiddenError(
            message="You do not have permission to perform this action.",
            code="INSUFFICIENT_PERMISSIONS",
        )


def check_tenant_admin(context: RequestContext) -> None:
    """Raise ForbiddenError unless primary role is tenant admin (DB name: admin)."""
    if context.role != "admin":
        raise ForbiddenError(
            message="You do not have permission to perform this action.",
            code="INSUFFICIENT_PERMISSIONS",
        )


def resolve_tenant_scope(
    context: RequestContext,
    requested_tenant_id: UUID | None = None,
) -> UUID | None:
    """Resolve the tenant ID to use for a tenant-scoped operation.

    - Tenant users: always return context.tenant_id; ignore client-supplied overrides.
    - Super admin (GLOBAL): may optionally select a target tenant via requested_tenant_id.
      Returning None means "no single-tenant filter" (global scope), NOT
      "filter WHERE tenant_id IS NULL".
    """
    if context.scope == SecurityScope.TENANT:
        if context.tenant_id is None:
            raise ForbiddenError(
                message="You do not have permission to perform this action.",
                code="MISSING_TENANT_SCOPE",
            )
        return context.tenant_id

    return requested_tenant_id


def ensure_tenant_resource_access(
    context: RequestContext,
    resource_tenant_id: UUID | None,
) -> None:
    """Ensure the authenticated context may access a tenant-owned resource.

    Cross-tenant access for normal users raises NotFoundError (404) to avoid
    leaking whether the resource exists in another tenant.

    Super admin (GLOBAL) may access any tenant-owned resource.
    resource_tenant_id of None is not treated as "super admin owned".
    """
    if context.scope == SecurityScope.GLOBAL and context.is_super_admin:
        return

    if context.tenant_id is None or resource_tenant_id is None:
        raise NotFoundError(
            message="The requested resource was not found.",
            code="NOT_FOUND",
        )

    if resource_tenant_id != context.tenant_id:
        raise NotFoundError(
            message="The requested resource was not found.",
            code="NOT_FOUND",
        )
