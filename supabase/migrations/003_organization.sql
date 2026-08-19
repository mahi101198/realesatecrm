-- =============================================================================
-- MIGRATION 003: Organization Tables
-- tenants → users → roles/permissions/role_permissions/user_roles → sales_agents
-- =============================================================================

-- ---------------------------------------------------------------------------
-- TENANTS
-- ---------------------------------------------------------------------------
-- Represents one real-estate company on the platform.
-- Every other business entity references tenant_id.
-- ---------------------------------------------------------------------------

create table public.tenants (
  id                  uuid primary key default gen_random_uuid(),
  name                text not null,                       -- Company name
  slug                text not null unique,                -- URL-safe identifier, e.g. "abc-realty"
  domain              text,                                -- Custom domain if applicable
  logo_url            text,
  primary_color       text,                                -- Hex color for white-labeling
  phone               text,
  email               text,
  address             text,
  city                text,
  state               text,
  country             text not null default 'India',
  pincode             text,
  gstin               text,
  timezone            text not null default 'Asia/Kolkata',
  locale              text not null default 'en-IN',
  currency            text not null default 'INR',
  plan                text not null default 'trial',       -- Subscription plan
  plan_expires_at     timestamptz,
  max_users           integer not null default 5,
  max_properties      integer not null default 500,
  max_ai_minutes      integer not null default 1000,       -- Monthly AI call minutes allowed
  is_active           boolean not null default true,
  settings            jsonb not null default '{}',         -- Tenant-specific flexible settings
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  deleted_at          timestamptz                          -- Soft delete
);

create index idx_tenants_slug   on public.tenants (slug);
create index idx_tenants_active on public.tenants (is_active) where is_active = true;

create trigger trg_tenants_updated_at
  before update on public.tenants
  for each row execute function public.set_updated_at();

comment on table public.tenants is
  'Top-level tenant (real-estate company) in the multi-tenant SaaS platform.';

-- ---------------------------------------------------------------------------
-- USERS
-- ---------------------------------------------------------------------------
-- Application-level user record linked to Supabase Auth.
-- Passwords live in auth.users, NOT here.
-- ---------------------------------------------------------------------------

create table public.users (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants (id) on delete restrict,
  auth_user_id        uuid unique references auth.users (id) on delete set null,
  name                text not null,
  email               text not null,
  phone               text,
  avatar_url          text,
  status              public.user_status not null default 'invited',
  preferred_language  text not null default 'en',
  timezone            text not null default 'Asia/Kolkata',
  last_seen_at        timestamptz,
  profile             jsonb not null default '{}',         -- Flexible profile fields
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  deleted_at          timestamptz,

  constraint uq_users_tenant_email unique (tenant_id, email)
);

create index idx_users_tenant_id    on public.users (tenant_id);
create index idx_users_auth_user_id on public.users (auth_user_id);
create index idx_users_tenant_email on public.users (tenant_id, email);
create index idx_users_tenant_status on public.users (tenant_id, status);

create trigger trg_users_updated_at
  before update on public.users
  for each row execute function public.set_updated_at();

comment on table public.users is
  'Application user record linked to Supabase Auth. Contains profile and '
  'tenant association. Passwords are never stored here.';

-- ---------------------------------------------------------------------------
-- ROLES
-- ---------------------------------------------------------------------------
-- System roles plus tenant-specific custom roles.
-- system_role = true means defined by the platform, not editable by tenant.
-- ---------------------------------------------------------------------------

create table public.roles (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid references public.tenants (id) on delete cascade,  -- NULL = system role
  name                text not null,                       -- e.g. "sales_agent", "manager"
  display_name        text not null,
  description         text,
  is_system_role      boolean not null default false,
  is_active           boolean not null default true,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),

  constraint uq_roles_tenant_name unique (tenant_id, name)
);

create index idx_roles_tenant_id on public.roles (tenant_id);

create trigger trg_roles_updated_at
  before update on public.roles
  for each row execute function public.set_updated_at();

comment on table public.roles is
  'RBAC roles. System roles (tenant_id IS NULL) are platform-defined. '
  'Tenants may define custom roles.';

-- Insert default system roles (no tenant, so no tenant_id)
insert into public.roles (name, display_name, description, is_system_role) values
  ('super_admin',    'Super Admin',    'Platform-level administrator.',           true),
  ('admin',          'Admin',          'Tenant administrator with full access.',  true),
  ('manager',        'Manager',        'Team manager with reporting access.',     true),
  ('sales_manager',  'Sales Manager',  'Manages sales team and campaigns.',       true),
  ('sales_agent',    'Sales Agent',    'Front-line sales representative.',        true),
  ('viewer',         'Viewer',         'Read-only access to CRM data.',           true);

-- ---------------------------------------------------------------------------
-- PERMISSIONS
-- ---------------------------------------------------------------------------
-- Granular permission codes used across the application.
-- ---------------------------------------------------------------------------

create table public.permissions (
  id                  uuid primary key default gen_random_uuid(),
  code                text not null unique,               -- e.g. "customer.read"
  name                text not null,
  description         text,
  resource            text not null,                     -- e.g. "customer"
  action              text not null,                     -- e.g. "read"
  created_at          timestamptz not null default now()
);

create index idx_permissions_resource on public.permissions (resource);
create index idx_permissions_code     on public.permissions (code);

comment on table public.permissions is
  'Granular permission codes. Permissions are platform-defined and shared '
  'across all tenants.';

-- Seed all system permissions
insert into public.permissions (code, name, description, resource, action) values
  -- Customer
  ('customer.read',             'Read Customers',             'View customer records.',                 'customer',      'read'),
  ('customer.create',           'Create Customers',           'Create new customer records.',           'customer',      'create'),
  ('customer.update',           'Update Customers',           'Edit customer records.',                 'customer',      'update'),
  ('customer.delete',           'Delete Customers',           'Soft-delete customer records.',          'customer',      'delete'),
  -- Lead
  ('lead.read',                 'Read Leads',                 'View lead records.',                     'lead',          'read'),
  ('lead.create',               'Create Leads',               'Create new leads.',                      'lead',          'create'),
  ('lead.update',               'Update Leads',               'Edit lead records.',                     'lead',          'update'),
  ('lead.assign',               'Assign Leads',               'Assign leads to sales agents.',          'lead',          'assign'),
  ('lead.delete',               'Delete Leads',               'Soft-delete leads.',                     'lead',          'delete'),
  -- Property
  ('property.read',             'Read Properties',            'View property inventory.',               'property',      'read'),
  ('property.create',           'Create Properties',          'Add new properties.',                    'property',      'create'),
  ('property.update',           'Update Properties',          'Edit property records.',                 'property',      'update'),
  ('property.delete',           'Delete Properties',          'Remove properties.',                     'property',      'delete'),
  -- Project
  ('project.read',              'Read Projects',              'View project records.',                  'project',       'read'),
  ('project.create',            'Create Projects',            'Add new projects.',                      'project',       'create'),
  ('project.update',            'Update Projects',            'Edit project records.',                  'project',       'update'),
  -- Campaign
  ('campaign.read',             'Read Campaigns',             'View campaigns.',                        'campaign',      'read'),
  ('campaign.create',           'Create Campaigns',           'Create new campaigns.',                  'campaign',      'create'),
  ('campaign.update',           'Update Campaigns',           'Edit campaign settings.',                'campaign',      'update'),
  ('campaign.start',            'Start Campaigns',            'Activate a campaign.',                   'campaign',      'start'),
  ('campaign.pause',            'Pause Campaigns',            'Pause a running campaign.',              'campaign',      'pause'),
  -- Call
  ('call.read',                 'Read Calls',                 'View call records.',                     'call',          'read'),
  ('call.recording.read',       'Listen to Recordings',       'Access call recordings.',                'call',          'recording.read'),
  -- Appointment
  ('appointment.read',          'Read Appointments',          'View appointments.',                     'appointment',   'read'),
  ('appointment.create',        'Create Appointments',        'Book appointments.',                     'appointment',   'create'),
  ('appointment.update',        'Update Appointments',        'Edit appointments.',                     'appointment',   'update'),
  ('appointment.cancel',        'Cancel Appointments',        'Cancel appointments.',                   'appointment',   'cancel'),
  -- Agent Config
  ('agent_config.read',         'Read Agent Configs',         'View AI agent configurations.',          'agent_config',  'read'),
  ('agent_config.update',       'Update Agent Configs',       'Modify AI agent configurations.',        'agent_config',  'update'),
  -- Sales Agent
  ('sales_agent.read',          'Read Sales Agents',          'View sales agent profiles.',             'sales_agent',   'read'),
  ('sales_agent.assign',        'Assign Sales Agents',        'Assign agents to leads.',                'sales_agent',   'assign'),
  -- Analytics
  ('analytics.read',            'Read Analytics',             'Access analytics and reports.',          'analytics',     'read'),
  -- Audit
  ('audit.read',                'Read Audit Logs',            'View audit log entries.',                'audit',         'read');

-- ---------------------------------------------------------------------------
-- ROLE_PERMISSIONS
-- ---------------------------------------------------------------------------

create table public.role_permissions (
  role_id             uuid not null references public.roles (id) on delete cascade,
  permission_id       uuid not null references public.permissions (id) on delete cascade,
  created_at          timestamptz not null default now(),
  primary key (role_id, permission_id)
);

create index idx_role_permissions_permission_id on public.role_permissions (permission_id);

comment on table public.role_permissions is
  'Many-to-many mapping of roles to permissions.';

-- Seed role_permissions for system roles
-- (done via a DO block to avoid massive duplication)
do $$
declare
  r_super_admin   uuid := (select id from public.roles where name = 'super_admin');
  r_admin         uuid := (select id from public.roles where name = 'admin');
  r_manager       uuid := (select id from public.roles where name = 'manager');
  r_sales_manager uuid := (select id from public.roles where name = 'sales_manager');
  r_sales_agent   uuid := (select id from public.roles where name = 'sales_agent');
  r_viewer        uuid := (select id from public.roles where name = 'viewer');
begin
  -- super_admin: all permissions
  insert into public.role_permissions (role_id, permission_id)
    select r_super_admin, id from public.permissions;

  -- admin: all permissions
  insert into public.role_permissions (role_id, permission_id)
    select r_admin, id from public.permissions;

  -- manager: most except agent_config update and audit
  insert into public.role_permissions (role_id, permission_id)
    select r_manager, id from public.permissions
    where code not in ('agent_config.update', 'audit.read', 'property.delete');

  -- sales_manager: lead mgmt, calls, appointments, analytics
  insert into public.role_permissions (role_id, permission_id)
    select r_sales_manager, id from public.permissions
    where code in (
      'customer.read','customer.create','customer.update',
      'lead.read','lead.create','lead.update','lead.assign',
      'property.read',
      'project.read',
      'campaign.read','campaign.create','campaign.update','campaign.start','campaign.pause',
      'call.read','call.recording.read',
      'appointment.read','appointment.create','appointment.update','appointment.cancel',
      'sales_agent.read','sales_agent.assign',
      'analytics.read'
    );

  -- sales_agent: basic CRM
  insert into public.role_permissions (role_id, permission_id)
    select r_sales_agent, id from public.permissions
    where code in (
      'customer.read','customer.create','customer.update',
      'lead.read','lead.create','lead.update',
      'property.read',
      'project.read',
      'call.read',
      'appointment.read','appointment.create','appointment.update','appointment.cancel',
      'sales_agent.read'
    );

  -- viewer: read-only
  insert into public.role_permissions (role_id, permission_id)
    select r_viewer, id from public.permissions
    where code like '%.read';
end;
$$;

-- ---------------------------------------------------------------------------
-- USER_ROLES
-- ---------------------------------------------------------------------------

create table public.user_roles (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants (id) on delete cascade,
  user_id             uuid not null references public.users (id) on delete cascade,
  role_id             uuid not null references public.roles (id) on delete restrict,
  granted_by          uuid references public.users (id) on delete set null,
  granted_at          timestamptz not null default now(),
  expires_at          timestamptz,                        -- Optional time-limited role grant
  is_active           boolean not null default true,

  constraint uq_user_roles_user_role unique (user_id, role_id)
);

create index idx_user_roles_tenant_id on public.user_roles (tenant_id);
create index idx_user_roles_user_id   on public.user_roles (user_id);
create index idx_user_roles_role_id   on public.user_roles (role_id);

comment on table public.user_roles is
  'Assigns roles to users within a tenant. Supports time-limited grants.';

-- ---------------------------------------------------------------------------
-- SALES_AGENTS
-- ---------------------------------------------------------------------------
-- Represents a human salesperson who can receive AI transfers and handle leads.
-- A sales_agent is always associated with a user record.
-- ---------------------------------------------------------------------------

create table public.sales_agents (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants (id) on delete restrict,
  user_id             uuid not null references public.users (id) on delete restrict,
  employee_code       text,
  specialization      text[],                             -- e.g. ARRAY['residential','plot']
  languages           text[] not null default ARRAY['hi','en'],
  availability        public.agent_availability not null default 'offline',
  current_call_id     uuid,                               -- FK added later (circular ref)
  last_active_at      timestamptz,
  notes               text,
  is_active           boolean not null default true,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  deleted_at          timestamptz,

  constraint uq_sales_agents_tenant_user unique (tenant_id, user_id)
);

create index idx_sales_agents_tenant_id   on public.sales_agents (tenant_id);
create index idx_sales_agents_user_id     on public.sales_agents (user_id);
create index idx_sales_agents_availability on public.sales_agents (tenant_id, availability)
  where is_active = true;

create trigger trg_sales_agents_updated_at
  before update on public.sales_agents
  for each row execute function public.set_updated_at();

comment on table public.sales_agents is
  'Human sales representatives. current_call_id FK is added in migration 006 '
  'after the calls table is created (circular reference resolution).';
