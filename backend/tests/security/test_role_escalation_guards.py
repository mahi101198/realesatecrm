"""Role-escalation and mass-assignment guards for Phase 2."""

from pathlib import Path

from app.users.schemas import MeResponse


def test_me_response_schema_has_no_writable_security_fields() -> None:
    """MeResponse is read-only identity; it must not model role/tenant updates."""
    fields = set(MeResponse.model_fields.keys())
    # Response may include role/tenant_id as read-only output.
    assert "id" in fields
    assert "permissions" in fields
    # There is no update schema in Phase 2 that accepts security fields.
    assert not hasattr(MeResponse, "model_validate_update")


def test_no_user_profile_update_schema_accepts_security_fields() -> None:
    """Phase 2 does not implement profile update APIs — regression guard.

    When a UserProfileUpdate schema is added later, it must not include
    role, tenant_id, permissions, or is_super_admin as assignable fields.
    """
    users_pkg = Path(__file__).resolve().parents[2] / "app" / "users"
    auth_pkg = Path(__file__).resolve().parents[2] / "app" / "auth"

    schema_files = list(users_pkg.glob("*.py")) + list(auth_pkg.glob("*.py"))
    forbidden_update_markers = (
        "class UserUpdate",
        "class UserProfileUpdate",
        "class MeUpdate",
    )
    for path in schema_files:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden_update_markers:
            assert marker not in text, (
                f"{path.name} defines {marker}; Phase 2 must not expose "
                "mass-assignable security field updates."
            )


def test_no_role_assign_endpoint_in_phase_2() -> None:
    """Role management endpoints are intentionally out of Phase 2 scope."""
    auth_router = (Path(__file__).resolve().parents[2] / "app" / "auth" / "router.py").read_text(
        encoding="utf-8"
    )
    assert "assign_role" not in auth_router
    assert "remove_role" not in auth_router
    assert "@router.patch" not in auth_router
    assert "@router.put" not in auth_router
    assert "@router.post" not in auth_router
