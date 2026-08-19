"""RBAC behavior tests using database permission codes."""

from uuid import uuid4

import pytest

from app.core.exceptions import ForbiddenError
from app.core.permissions import check_permission
from app.core.request_context import RequestContext, SecurityScope


def _ctx(role: str, permissions: set[str], *, is_super_admin: bool = False) -> RequestContext:
    return RequestContext(
        request_id="rbac-test",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=None if is_super_admin else uuid4(),
        role=role,
        permissions=frozenset(permissions),
        is_super_admin=is_super_admin,
        scope=SecurityScope.GLOBAL if is_super_admin else SecurityScope.TENANT,
    )


# Representative permission sets mirroring migration 003 seed (not invented).
VIEWER_PERMS = {"customer.read", "lead.read", "property.read", "project.read"}
SALES_AGENT_PERMS = {
    "customer.read",
    "customer.create",
    "customer.update",
    "lead.read",
    "lead.create",
    "lead.update",
    "property.read",
    "project.read",
    "call.read",
    "appointment.read",
    "appointment.create",
    "appointment.update",
    "appointment.cancel",
    "sales_agent.read",
}
SUPER_ADMIN_PLATFORM = {
    "platform.tenant.read",
    "platform.tenant.create",
    "lead.read",
}


@pytest.mark.parametrize(
    ("role", "permissions", "allowed", "denied"),
    [
        ("viewer", VIEWER_PERMS, "lead.read", "lead.update"),
        ("sales_agent", SALES_AGENT_PERMS, "lead.update", "lead.delete"),
        ("sales_agent", SALES_AGENT_PERMS, "appointment.create", "audit.read"),
        ("super_admin", SUPER_ADMIN_PLATFORM, "platform.tenant.read", "platform.billing.read"),
    ],
)
def test_role_permission_matrix(
    role: str,
    permissions: set[str],
    allowed: str,
    denied: str,
) -> None:
    ctx = _ctx(role, permissions, is_super_admin=(role == "super_admin"))
    check_permission(ctx, allowed)
    with pytest.raises(ForbiddenError) as exc_info:
        check_permission(ctx, denied)
    assert exc_info.value.status_code == 403
