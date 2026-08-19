-- =============================================================================
-- MIGRATION 024: Locations
--
-- Today `projects` carries city/locality/district/state as plain text
-- columns, which drifts ("Gurgaon" vs "gurugram" vs "Gurugram") and cannot
-- support location-level rollups ("how many units did we sell in Gurgaon
-- across all projects") or location-level metadata. This migration adds a
-- real `locations` table that a tenant defines once, and links `projects`
-- to it via a new, nullable, additive column.
--
-- Explicitly NOT done here (per scope discussion with the business owner):
--   - The existing projects.city / locality / district / state text columns
--     are NOT removed or renamed. Existing app code (app/projects/*.py)
--     keeps working unchanged; location_id is an additive forward path.
--   - No backfill of projects.location_id from the existing text columns --
--     that requires a data-matching decision (fuzzy city-name resolution)
--     that belongs in a follow-up, not a schema migration.
--   - No app-layer (repository/service/router) changes.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. LOCATIONS
-- ---------------------------------------------------------------------------

create table public.locations (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  name                 text not null,                       -- e.g. "Gurgaon", "Whitefield"
  city                 text not null,
  state                text,
  country              text not null default 'India',       -- matches tenants.country / customers.country
  pincode              text,                                 -- matches tenants.pincode / customers.pincode / projects.pincode naming
  is_active            boolean not null default true,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),

  constraint uq_locations_tenant_id unique (tenant_id, id)
);

create index idx_locations_tenant_id on public.locations (tenant_id);
create index idx_locations_tenant_city on public.locations (tenant_id, city);

-- Dedup guard: no existing table in this schema normalizes text for
-- case/whitespace-insensitive uniqueness (customers dedups phone numbers by
-- exact match on a partial unique index -- see uq_customers_tenant_phone_active
-- in migration 014). There is no established normalization expression to
-- reuse, so this introduces one: lower(trim(...)) on both name and city,
-- which is the standard Postgres idiom for case/whitespace-insensitive
-- uniqueness. Not partial on is_active -- see comment below.
create unique index uq_locations_tenant_name_city
  on public.locations (tenant_id, lower(trim(name)), lower(trim(city)));

create trigger trg_locations_updated_at
  before update on public.locations
  for each row execute function public.set_updated_at();

comment on table public.locations is
  'Tenant-defined location (city/area), the structured replacement path for '
  'the free-text city/locality fields on projects. A tenant defines each '
  'location once; projects reference it via projects.location_id.';

comment on index public.uq_locations_tenant_name_city is
  'Prevents duplicate locations per tenant that differ only by case or '
  'surrounding whitespace (e.g. "Gurgaon" vs "gurgaon "). Deliberately NOT '
  'partial on is_active: locations has no deleted_at/identity-removal concept '
  '(unlike customers), so deactivating a location (is_active = false) should '
  'lead to reactivating the existing row, not creating a duplicate with the '
  'same normalized name.';

comment on column public.locations.is_active is
  'Soft-deactivation flag. Use this instead of deleting rows -- locations may '
  'be referenced by historical projects and should not disappear from the '
  'database, only from active-selection UI lists.';

-- ---------------------------------------------------------------------------
-- 2. PROJECTS: additive column linking to locations
-- ---------------------------------------------------------------------------
-- Existing city/locality/district/state text columns are untouched and
-- remain the source of truth for existing app code. location_id is the
-- forward path toward a structured location; nullable so no backfill is
-- required by this migration.
-- ---------------------------------------------------------------------------

alter table public.projects
  add column location_id uuid;

-- ON DELETE SET NULL: location_id is a nullable, optional convenience link
-- (same treatment as created_by/uploaded_by/verified_by elsewhere in this
-- schema), not a required structural relationship like project_type_id or
-- tenant_id (which use ON DELETE RESTRICT). Losing the location reference
-- should not block or cascade-delete the project.
alter table public.projects
  add constraint fk_projects_location_tenant
  foreign key (tenant_id, location_id) references public.locations (tenant_id, id) on delete set null;

create index idx_projects_location on public.projects (tenant_id, location_id) where location_id is not null;

comment on column public.projects.location_id is
  'Optional structured location (see public.locations), the forward path '
  'away from free-text city/locality/district/state. Those existing text '
  'columns are kept as-is for backward compatibility and are not backfilled '
  'by this migration; new/updated projects should set both until app code '
  'migrates to reading location_id as the source of truth.';

-- ---------------------------------------------------------------------------
-- 3. PERMISSIONS
-- ---------------------------------------------------------------------------

insert into public.permissions (code, name, description, resource, action) values
  ('location.read',    'Read Locations',   'View tenant location definitions.',        'location', 'read'),
  ('location.create',  'Create Locations', 'Define a new location for the tenant.',    'location', 'create'),
  ('location.update',  'Update Locations', 'Edit or deactivate a location.',           'location', 'update')
on conflict (code) do nothing;

-- Grants mirror how project.read/property.read are already distributed
-- (broadly readable; only admin/sales_manager can define or edit locations),
-- and the same explicit-grant pattern used for the property_* permissions
-- in migration 023 (new codes are not retroactively picked up by roles
-- whose earlier grant was a point-in-time SELECT).

-- admin: full access.
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'admin' and r.is_system_role = true
  and  p.code in ('location.read', 'location.create', 'location.update')
on conflict do nothing;

-- sales_manager: full access (defines/maintains the location list alongside projects).
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'sales_manager' and r.is_system_role = true
  and  p.code in ('location.read', 'location.create', 'location.update')
on conflict do nothing;

-- manager, sales_agent, viewer: read-only, matching how project.read/property.read are granted.
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name in ('manager', 'sales_agent', 'viewer') and r.is_system_role = true
  and  p.code = 'location.read'
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- 4. ROW LEVEL SECURITY
-- ---------------------------------------------------------------------------
-- Pattern matches migrations 010/018/019/023: service_role bypass, then
-- is_super_admin() OR (tenant match AND permission) for authenticated users.
-- No DELETE policy for authenticated users -- deactivate via is_active
-- instead of deleting rows (same philosophy as the rest of this schema).
-- ---------------------------------------------------------------------------

alter table public.locations enable row level security;

create policy "locations: service_role full access"
  on public.locations for all
  to service_role using (true) with check (true);

create policy "locations: read"
  on public.locations for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('location.read'))
  );

create policy "locations: authorized can insert"
  on public.locations for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('location.create'))
  );

create policy "locations: authorized can update"
  on public.locations for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('location.update'))
  );
