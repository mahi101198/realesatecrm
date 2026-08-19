# Security Flow (Phase 2)

Short reference for authentication and authorization.

```text
HTTP request
    → Bearer JWT
    → verify signature / exp / audience / subject
    → reject service_role bearer
    → load application user by auth_user_id (JWT sub)
    → require status=active, not deleted, active role(s)
    → require active tenant (tenant users only)
    → RequestContext (server-built)
    → require_permission(...)
    → ensure_tenant_resource_access(...)
    → endpoint / service
```

## Identity sources

| Trusted | Never trusted from client |
|---------|---------------------------|
| Verified JWT `sub` | `role`, `is_super_admin` |
| `users` / `user_roles` / `permissions` | `tenant_id` query/body/header |
| `tenants.is_active` | JWT application role claims |

## Scopes

- **GLOBAL** — `super_admin`, `tenant_id = None`. May access any tenant resource. Still needs DB permissions.
- **TENANT** — normal users. Always scoped to `RequestContext.tenant_id`. Client tenant overrides are ignored.

Super admin global scope is **not** `WHERE tenant_id IS NULL`.

## Cross-tenant lookups

For tenant users, inaccessible or other-tenant resources return **404 Not Found** (not 403) so the API is not an existence oracle.

## Key modules

- `app/core/security.py` — JWT verification
- `app/auth/dependencies.py` — `get_current_user`, `get_request_context_dep`, `require_permission`
- `app/core/permissions.py` — permission codes, tenant/resource helpers
- `app/users/repository.py` — one-shot RBAC load
- `GET /api/v1/me` — safe identity from authenticated context only
