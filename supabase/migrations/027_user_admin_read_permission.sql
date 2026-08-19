-- =============================================================================
-- MIGRATION 027: user.read Permission
--
-- app/users/ has had no HTTP surface at all until now (role assign/remove,
-- user listing were unreachable via the API despite the service/repository
-- layer existing). Adding that surface needs a read permission for
-- GET /users and GET /users/{id} -- user.create/user.update already exist
-- (migration 016) and, per that migration's own description ("Update CRM
-- user profiles and roles"), user.update is ALREADY the permission that
-- gates role assignment at the RLS layer (see user_roles insert/update/
-- delete policies in migration 018) -- no separate role.assign permission
-- is introduced here, user.update is reused for that exactly as the DB
-- schema already intends.
--
-- Granted to the same tight admin-only set user.create/user.update use
-- (migration 016): user management is sensitive, not general CRM read
-- access, so this deliberately does NOT follow the broader grant pattern
-- used for property/location/etc. read permissions.
-- =============================================================================

insert into public.permissions (code, name, description, resource, action) values
  ('user.read', 'Read Users', 'View CRM staff user accounts and role assignments.', 'user', 'read')
on conflict (code) do nothing;

insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r
join   public.permissions p on p.code = 'user.read'
where  r.name in ('super_admin', 'admin')
  and  r.is_system_role = true
on conflict do nothing;
