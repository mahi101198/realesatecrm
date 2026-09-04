-- =============================================================================
-- MIGRATION 035: Role & Permission Management Permissions
--
-- Adds role.read / role.create / role.update / role.delete so a tenant admin
-- can manage custom roles and their permission grants from the dashboard
-- (Settings > Policy Manager). Granted to admin and super_admin only --
-- role management is deliberately not extended to manager/sales_manager by
-- default; a tenant admin can create a custom role for that if they want to
-- delegate it, since role.create/role.update only ever operate on
-- tenant-owned, non-system roles (enforced in app/roles/service.py, not by a
-- DB trigger -- there is no existing trigger guarding roles/role_permissions
-- mutation the way trg_fn_prevent_role_escalation guards user_roles).
-- =============================================================================

insert into public.permissions (code, name, description, resource, action) values
  ('role.read',   'Read Roles',   'View roles and their permission grants.',        'role', 'read'),
  ('role.create', 'Create Roles', 'Create new tenant-custom roles.',                'role', 'create'),
  ('role.update', 'Update Roles', 'Edit tenant-custom roles and their permissions.','role', 'update'),
  ('role.delete', 'Delete Roles', 'Deactivate tenant-custom roles.',                'role', 'delete')
on conflict (code) do nothing;

do $$
declare
  r_super_admin uuid := (select id from public.roles where name = 'super_admin' and is_system_role = true);
  r_admin       uuid := (select id from public.roles where name = 'admin' and is_system_role = true);
  v_new_perm_ids uuid[] := (
    select array_agg(id) from public.permissions
    where code in ('role.read', 'role.create', 'role.update', 'role.delete')
  );
begin
  insert into public.role_permissions (role_id, permission_id)
    select r_super_admin, unnest(v_new_perm_ids)
  on conflict (role_id, permission_id) do nothing;

  insert into public.role_permissions (role_id, permission_id)
    select r_admin, unnest(v_new_perm_ids)
  on conflict (role_id, permission_id) do nothing;
end;
$$;
