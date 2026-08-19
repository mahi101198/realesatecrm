"""Unit tests for UserAdminService, with a specific focus on role-escalation
prevention -- the DB trigger that implements these same rules
(trg_fn_prevent_role_escalation, migration 019) explicitly bypasses
service_role, and this backend always connects as service_role, so THIS
service-layer logic is the only enforcement that actually runs.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.request_context import RequestContext, SecurityScope
from app.users.schemas import RoleAssignRequest
from app.users.service import UserAdminService


def _context(
    *, user_id=None, tenant_id=None, is_super_admin=False, permissions=frozenset()
) -> RequestContext:
    return RequestContext(
        request_id="test",
        user_id=user_id or uuid4(),
        auth_user_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        role="admin",
        permissions=frozenset(permissions),
        is_super_admin=is_super_admin,
        scope=SecurityScope.GLOBAL if is_super_admin else SecurityScope.TENANT,
    )


def _service() -> UserAdminService:
    return UserAdminService(AsyncMock())


@pytest.mark.asyncio
async def test_assign_role_blocks_self_assignment_for_non_super_admin() -> None:
    """Verify a caller can never assign a role to themselves, even a
    low-privilege role -- self-role-modification is blocked outright."""
    service = _service()
    tenant_id = uuid4()
    caller_id = uuid4()
    context = _context(user_id=caller_id, tenant_id=tenant_id, permissions={"user.update"})
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": caller_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )

    with pytest.raises(ForbiddenError) as exc_info:
        await service.assign_role(context, tenant_id, caller_id, RoleAssignRequest(role_id=uuid4()))
    assert exc_info.value.code == "SELF_ROLE_ESCALATION_BLOCKED"


@pytest.mark.asyncio
async def test_assign_role_blocks_non_super_admin_granting_super_admin() -> None:
    """THE critical test: a tenant admin (or anyone else who isn't a
    platform super_admin) must never be able to grant the super_admin role
    to another user, regardless of what permissions they otherwise hold."""
    service = _service()
    tenant_id = uuid4()
    target_user_id = uuid4()
    context = _context(
        user_id=uuid4(), tenant_id=tenant_id, is_super_admin=False, permissions={"user.update"}
    )
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": target_user_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    service.repository.get_role_by_id = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": None,
            "name": "super_admin",
            "is_system_role": True,
            "is_active": True,
        }
    )
    assign_mock = AsyncMock()
    service.repository.assign_role = assign_mock

    with pytest.raises(ForbiddenError) as exc_info:
        await service.assign_role(
            context, tenant_id, target_user_id, RoleAssignRequest(role_id=uuid4())
        )
    assert exc_info.value.code == "SUPER_ADMIN_ROLE_ESCALATION_BLOCKED"
    assign_mock.assert_not_called()


@pytest.mark.asyncio
async def test_assign_role_allows_super_admin_to_grant_super_admin() -> None:
    """Verify a genuine platform super_admin CAN grant the super_admin role."""
    service = _service()
    tenant_id = uuid4()
    target_user_id = uuid4()
    context = _context(user_id=uuid4(), tenant_id=tenant_id, is_super_admin=True)
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": target_user_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    service.repository.get_role_by_id = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": None,
            "name": "super_admin",
            "is_system_role": True,
            "is_active": True,
        }
    )
    service.repository.assign_role = AsyncMock(
        return_value={
            "id": uuid4(),
            "user_id": target_user_id,
            "role_id": uuid4(),
            "granted_by": context.user_id,
            "granted_at": "2026-01-01T00:00:00Z",
            "expires_at": None,
            "is_active": True,
        }
    )

    result = await service.assign_role(
        context, tenant_id, target_user_id, RoleAssignRequest(role_id=uuid4())
    )
    assert result.role_name == "super_admin"


@pytest.mark.asyncio
async def test_assign_role_blocks_admin_grant_without_authority() -> None:
    """Verify granting the 'admin' role requires super_admin or user.update --
    tested directly against the service (not just relying on the router's
    permission gate) since this check is documented as a safety net for if
    that gate is ever loosened."""
    service = _service()
    tenant_id = uuid4()
    target_user_id = uuid4()
    context = _context(user_id=uuid4(), tenant_id=tenant_id, permissions=set())
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": target_user_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    service.repository.get_role_by_id = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": None,
            "name": "admin",
            "is_system_role": True,
            "is_active": True,
        }
    )

    with pytest.raises(ForbiddenError) as exc_info:
        await service.assign_role(
            context, tenant_id, target_user_id, RoleAssignRequest(role_id=uuid4())
        )
    assert exc_info.value.code == "ADMIN_ROLE_ESCALATION_BLOCKED"


@pytest.mark.asyncio
async def test_assign_role_allows_ordinary_role_grant() -> None:
    """Verify the normal case (granting sales_agent/viewer/etc.) succeeds
    for a tenant admin with user.update."""
    service = _service()
    tenant_id = uuid4()
    target_user_id = uuid4()
    context = _context(user_id=uuid4(), tenant_id=tenant_id, permissions={"user.update"})
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": target_user_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    service.repository.get_role_by_id = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": None,
            "name": "sales_agent",
            "is_system_role": True,
            "is_active": True,
        }
    )
    service.repository.assign_role = AsyncMock(
        return_value={
            "id": uuid4(),
            "user_id": target_user_id,
            "role_id": uuid4(),
            "granted_by": context.user_id,
            "granted_at": "2026-01-01T00:00:00Z",
            "expires_at": None,
            "is_active": True,
        }
    )

    result = await service.assign_role(
        context, tenant_id, target_user_id, RoleAssignRequest(role_id=uuid4())
    )
    assert result.role_name == "sales_agent"


@pytest.mark.asyncio
async def test_assign_role_rejects_role_belonging_to_another_tenant() -> None:
    """Verify a custom role belonging to a DIFFERENT tenant cannot be
    assigned, and the response doesn't leak that the role exists elsewhere."""
    service = _service()
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    target_user_id = uuid4()
    context = _context(user_id=uuid4(), tenant_id=tenant_id, permissions={"user.update"})
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": target_user_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    service.repository.get_role_by_id = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": other_tenant_id,
            "name": "custom_role",
            "is_system_role": False,
            "is_active": True,
        }
    )

    with pytest.raises(NotFoundError) as exc_info:
        await service.assign_role(
            context, tenant_id, target_user_id, RoleAssignRequest(role_id=uuid4())
        )
    assert exc_info.value.code == "ROLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_remove_role_blocks_self_removal() -> None:
    """Verify a caller cannot remove their own role grants either."""
    service = _service()
    tenant_id = uuid4()
    caller_id = uuid4()
    context = _context(user_id=caller_id, tenant_id=tenant_id, permissions={"user.update"})
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": caller_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )

    with pytest.raises(ForbiddenError) as exc_info:
        await service.remove_role(context, tenant_id, caller_id, uuid4())
    assert exc_info.value.code == "SELF_ROLE_ESCALATION_BLOCKED"


@pytest.mark.asyncio
async def test_remove_role_success() -> None:
    """Verify removing another user's role grant succeeds."""
    service = _service()
    tenant_id = uuid4()
    target_user_id = uuid4()
    context = _context(user_id=uuid4(), tenant_id=tenant_id, permissions={"user.update"})
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": target_user_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    service.repository.remove_role = AsyncMock(return_value=True)

    await service.remove_role(context, tenant_id, target_user_id, uuid4())  # must not raise


@pytest.mark.asyncio
async def test_remove_role_raises_not_found_for_missing_grant() -> None:
    """Verify removing a non-existent/already-inactive role grant 404s."""
    service = _service()
    tenant_id = uuid4()
    target_user_id = uuid4()
    context = _context(user_id=uuid4(), tenant_id=tenant_id, permissions={"user.update"})
    service.repository.get_user_detail = AsyncMock(
        return_value={
            "id": target_user_id,
            "tenant_id": tenant_id,
            "name": "x",
            "email": "x@x.com",
            "phone": None,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    service.repository.remove_role = AsyncMock(return_value=False)

    with pytest.raises(NotFoundError) as exc_info:
        await service.remove_role(context, tenant_id, target_user_id, uuid4())
    assert exc_info.value.code == "ROLE_GRANT_NOT_FOUND"
