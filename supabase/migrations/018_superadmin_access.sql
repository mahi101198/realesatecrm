-- =============================================================================
-- MIGRATION 018: Super Admin Global Access + Tenant Admin Tenant-Level Access
--
-- PROBLEM STATEMENT:
--   Currently super_admin and admin are treated identically by RLS. Both are
--   locked inside one tenant because users.tenant_id is NOT NULL.
--
-- CHANGES:
--   1. Make users.tenant_id nullable → super_admin has no tenant affiliation
--   2. Add is_super_admin() helper function (reads from user_roles)
--   3. Add platform-level permissions (platform.tenant.read, etc.)
--   4. Update get_current_tenant_id() to return NULL for super_admin
--   5. Update ALL RLS policies with a super_admin bypass branch
--   6. RLS policies for tenants table: super_admin can read ALL, admin cannot
--
-- RESULT:
--   super_admin → global access across all tenants via JWT (UI + API)
--   admin       → scoped to their own tenant only
--   All other roles → scoped to their tenant
-- =============================================================================

-- ---------------------------------------------------------------------------
-- STEP 1: Make users.tenant_id nullable
--   super_admin users have no tenant affiliation (tenant_id = NULL).
--   All existing users keep their tenant_id. Only platform accounts
--   created without a tenant get NULL.
-- ---------------------------------------------------------------------------

-- Drop the NOT NULL constraint
alter table public.users
  alter column tenant_id drop not null;

-- Update the FK constraint to reflect nullable (no change needed, FK allows NULL)
comment on column public.users.tenant_id is
  'The tenant this user belongs to. NULL for platform-level super_admin accounts '
  'who have no tenant affiliation and can access all tenants.';

-- ---------------------------------------------------------------------------
-- STEP 2: Add is_super_admin() helper function
--   Called inside RLS policies to grant cross-tenant access.
--   Uses security definer to read user_roles without triggering RLS on itself.
-- ---------------------------------------------------------------------------

create or replace function public.is_super_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from   public.user_roles ur
    join   public.roles r on r.id = ur.role_id
    where  ur.user_id = (
             select id
             from   public.users
             where  auth_user_id = auth.uid()
             limit  1
           )
      and  r.name       = 'super_admin'
      and  r.is_system_role = true
      and  ur.is_active = true
      and  (ur.expires_at is null or ur.expires_at > now())
  );
$$;

comment on function public.is_super_admin() is
  'Returns TRUE if the current Supabase JWT user has an active super_admin role. '
  'super_admin users have global cross-tenant access. '
  'Called inside RLS policies to bypass tenant filtering.';

-- ---------------------------------------------------------------------------
-- STEP 3: Update get_current_tenant_id()
--   Now returns NULL for super_admin (they have no tenant).
--   All existing RLS policies use this function — returning NULL would
--   cause tenant_id = NULL comparisons to fail (NULL ≠ NULL in SQL).
--   Therefore we keep the function returning their tenant_id (NULL for super_admin)
--   and handle super_admin via OR public.is_super_admin() in each policy.
-- ---------------------------------------------------------------------------

-- No change needed to get_current_tenant_id() itself —
-- it already returns users.tenant_id which will be NULL for super_admin.
-- The RLS policies need the OR is_super_admin() branch instead.

-- ---------------------------------------------------------------------------
-- STEP 4: Add platform-level permissions
--   super_admin gets these; no other role does.
-- ---------------------------------------------------------------------------

insert into public.permissions (code, name, description, resource, action) values
  ('platform.tenant.read',    'Read All Tenants',      'View all tenant accounts.',           'platform', 'tenant.read'),
  ('platform.tenant.create',  'Create Tenants',        'Onboard a new tenant.',               'platform', 'tenant.create'),
  ('platform.tenant.update',  'Update Tenants',        'Modify any tenant settings.',         'platform', 'tenant.update'),
  ('platform.tenant.suspend', 'Suspend Tenants',       'Deactivate or suspend a tenant.',     'platform', 'tenant.suspend'),
  ('platform.user.impersonate','Impersonate Users',    'Log in as any tenant user.',          'platform', 'user.impersonate'),
  ('platform.billing.read',   'Read All Billing',      'View billing and usage for all.',     'platform', 'billing.read'),
  ('platform.analytics.read', 'Global Analytics',      'View cross-tenant analytics.',        'platform', 'analytics.read')
on conflict (code) do nothing;

-- Grant all platform permissions to super_admin only
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'super_admin'
  and  r.is_system_role = true
  and  p.code like 'platform.%'
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- STEP 5: RLS POLICY UPDATES
--
-- Pattern used throughout:
--   USING (
--     public.is_super_admin()                       -- super_admin: global access
--     OR tenant_id = public.get_current_tenant_id() -- everyone else: own tenant only
--   )
--
-- This is applied to every tenant-owned table.
-- ---------------------------------------------------------------------------

-- ---- TENANTS TABLE ----
-- super_admin: see all tenants
-- admin/others: see only their own tenant
-- New: super_admin can also INSERT and UPDATE tenants

drop policy if exists "tenants: users see own tenant" on public.tenants;

create policy "tenants: users see own tenant"
  on public.tenants for select
  to authenticated
  using (
    public.is_super_admin()                      -- super_admin: all tenants
    or id = public.get_current_tenant_id()       -- others: only their tenant
  );

create policy "tenants: super_admin can insert"
  on public.tenants for insert
  to authenticated
  with check (public.is_super_admin());

create policy "tenants: super_admin can update"
  on public.tenants for update
  to authenticated
  using  (public.is_super_admin())
  with check (public.is_super_admin());

-- ---- USERS TABLE ----
drop policy if exists "users: tenant members can read"  on public.users;
drop policy if exists "users: admins can insert"        on public.users;
drop policy if exists "users: admins can update"        on public.users;

create policy "users: tenant members can read"
  on public.users for select
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

create policy "users: admins can insert"
  on public.users for insert
  to authenticated
  with check (
    (public.is_super_admin())
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('user.create')
    )
  );

create policy "users: admins can update"
  on public.users for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    (public.is_super_admin())
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('user.update')
    )
  );

-- ---- ROLES TABLE ----
drop policy if exists "roles: authenticated can read system or own tenant roles" on public.roles;

create policy "roles: authenticated can read system or own tenant roles"
  on public.roles for select
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id is null                          -- system roles: visible to all
    or tenant_id = public.get_current_tenant_id() -- tenant-custom roles: own tenant only
  );

-- ---- USER_ROLES TABLE ----
drop policy if exists "user_roles: tenant can read"  on public.user_roles;
drop policy if exists "user_roles: admins can insert" on public.user_roles;
drop policy if exists "user_roles: admins can update" on public.user_roles;
drop policy if exists "user_roles: admins can delete" on public.user_roles;

create policy "user_roles: read"
  on public.user_roles for select
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

create policy "user_roles: admins can insert"
  on public.user_roles for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('user.update')
    )
  );

create policy "user_roles: admins can update"
  on public.user_roles for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('user.update')
    )
  );

create policy "user_roles: admins can delete"
  on public.user_roles for delete
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('user.update')
    )
  );

-- ---- SALES_AGENTS ----
drop policy if exists "sales_agents: tenant read"           on public.sales_agents;
drop policy if exists "sales_agents: managers can insert/update" on public.sales_agents;
drop policy if exists "sales_agents: managers can update"   on public.sales_agents;

create policy "sales_agents: read"
  on public.sales_agents for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('sales_agent.read')
    )
  );

create policy "sales_agents: managers can insert"
  on public.sales_agents for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('sales_agent.assign')
    )
  );

create policy "sales_agents: managers can update"
  on public.sales_agents for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

-- ---- PROJECTS ----
drop policy if exists "projects: tenant read"          on public.projects;
drop policy if exists "projects: authorized can insert" on public.projects;
drop policy if exists "projects: authorized can update" on public.projects;

create policy "projects: read"
  on public.projects for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('project.read')
      and deleted_at is null
    )
  );

create policy "projects: authorized can insert"
  on public.projects for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('project.create')
    )
  );

create policy "projects: authorized can update"
  on public.projects for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('project.update')
    )
  );

-- ---- PROPERTIES ----
drop policy if exists "properties: tenant read"           on public.properties;
drop policy if exists "properties: authorized can insert"  on public.properties;
drop policy if exists "properties: authorized can update"  on public.properties;
drop policy if exists "properties: admins can delete"      on public.properties;

create policy "properties: read"
  on public.properties for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('property.read')
      and deleted_at is null
    )
  );

create policy "properties: authorized can insert"
  on public.properties for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('property.create')
    )
  );

create policy "properties: authorized can update"
  on public.properties for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('property.update')
    )
  );

create policy "properties: admins can delete"
  on public.properties for delete
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('property.delete')
    )
  );

-- ---- CUSTOMERS ----
drop policy if exists "customers: tenant read"           on public.customers;
drop policy if exists "customers: authorized can insert"  on public.customers;
drop policy if exists "customers: authorized can update"  on public.customers;

create policy "customers: read"
  on public.customers for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('customer.read')
      and deleted_at is null
    )
  );

create policy "customers: authorized can insert"
  on public.customers for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('customer.create')
    )
  );

create policy "customers: authorized can update"
  on public.customers for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('customer.update')
    )
  );

-- ---- LEADS ----
drop policy if exists "leads: tenant read"           on public.leads;
drop policy if exists "leads: authorized can insert"  on public.leads;
drop policy if exists "leads: authorized can update"  on public.leads;

create policy "leads: read"
  on public.leads for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('lead.read')
      and deleted_at is null
    )
  );

create policy "leads: authorized can insert"
  on public.leads for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('lead.create')
    )
  );

create policy "leads: authorized can update"
  on public.leads for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('lead.update')
    )
  );

-- ---- CAMPAIGNS ----
drop policy if exists "campaigns: tenant read"           on public.campaigns;
drop policy if exists "campaigns: authorized can insert"  on public.campaigns;
drop policy if exists "campaigns: authorized can update"  on public.campaigns;

create policy "campaigns: read"
  on public.campaigns for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('campaign.read')
      and deleted_at is null
    )
  );

create policy "campaigns: authorized can insert"
  on public.campaigns for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('campaign.create')
    )
  );

create policy "campaigns: authorized can update"
  on public.campaigns for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('campaign.update')
    )
  );

-- ---- CALLS (read-only for UI, write via service_role) ----
drop policy if exists "calls: tenant read" on public.calls;

create policy "calls: read"
  on public.calls for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('call.read')
    )
  );

-- ---- AGENT_CONFIGS ----
drop policy if exists "agent_configs: tenant read"             on public.agent_configs;
drop policy if exists "agent_configs: authorized can insert/update" on public.agent_configs;
drop policy if exists "agent_configs: authorized can update"   on public.agent_configs;

create policy "agent_configs: read"
  on public.agent_configs for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('agent_config.read')
      and deleted_at is null
    )
  );

create policy "agent_configs: authorized can insert"
  on public.agent_configs for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('agent_config.update')
    )
  );

create policy "agent_configs: authorized can update"
  on public.agent_configs for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('agent_config.update')
    )
  );

-- ---- AUDIT_LOGS ----
drop policy if exists "audit_logs: authorized tenant read" on public.audit_logs;
drop policy if exists "audit_logs: system can insert"      on public.audit_logs;

create policy "audit_logs: read"
  on public.audit_logs for select
  to authenticated
  using (
    public.is_super_admin()                       -- super_admin: all tenants' logs
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('audit.read')     -- tenant admin: own logs only
    )
  );

create policy "audit_logs: system can insert"
  on public.audit_logs for insert
  to authenticated
  with check (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

-- ---- INTEGRATIONS ----
drop policy if exists "integrations: admins can manage" on public.integrations;

create policy "integrations: admins can manage"
  on public.integrations for all
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('agent_config.read')
    )
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('agent_config.update')
    )
  );

-- ---- WEBHOOK_EVENTS ----
drop policy if exists "webhook_events: admins can read" on public.webhook_events;

create policy "webhook_events: admins can read"
  on public.webhook_events for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('audit.read')
    )
  );

-- ---- AI_USAGE_EVENTS ----
drop policy if exists "ai_usage: tenant analytics read" on public.ai_usage_events;

create policy "ai_usage: read"
  on public.ai_usage_events for select
  to authenticated
  using (
    public.is_super_admin()                       -- sees all tenants (global cost view)
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('analytics.read')
    )
  );

-- ---- APPOINTMENTS ----
drop policy if exists "appointments: tenant read"           on public.appointments;
drop policy if exists "appointments: authorized can insert"  on public.appointments;
drop policy if exists "appointments: authorized can update"  on public.appointments;

create policy "appointments: read"
  on public.appointments for select
  to authenticated
  using (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('appointment.read')
    )
  );

create policy "appointments: authorized can insert"
  on public.appointments for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('appointment.create')
    )
  );

create policy "appointments: authorized can update"
  on public.appointments for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (
      tenant_id = public.get_current_tenant_id()
      and public.has_permission('appointment.update')
    )
  );

-- ---- Remaining tables: super_admin read-all, tenant access preserved ----
-- Pattern: drop old policy, recreate with is_super_admin() OR tenant filter

-- property_status_history
drop policy if exists "prop_status_hist: tenant read" on public.property_status_history;
create policy "prop_status_hist: read"
  on public.property_status_history for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.read')));

-- property_prices
drop policy if exists "property_prices: tenant read" on public.property_prices;
create policy "property_prices: read"
  on public.property_prices for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.read')));

-- property_price_history
drop policy if exists "price_history: tenant read" on public.property_price_history;
create policy "price_history: read"
  on public.property_price_history for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.read')));

-- customer_preferences
drop policy if exists "customer_prefs: tenant access" on public.customer_preferences;
create policy "customer_prefs: access"
  on public.customer_preferences for all to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('customer.read')))
  with check (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('customer.update')));

-- customer_notes
drop policy if exists "customer_notes: tenant access" on public.customer_notes;
create policy "customer_notes: access"
  on public.customer_notes for all to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('customer.read')))
  with check (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('customer.update')));

-- lead_property_interests
drop policy if exists "lead_prop_interests: tenant access" on public.lead_property_interests;
create policy "lead_prop_interests: access"
  on public.lead_property_interests for all to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.read')))
  with check (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.update')));

-- lead_notes
drop policy if exists "lead_notes: tenant access" on public.lead_notes;
create policy "lead_notes: access"
  on public.lead_notes for all to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.read')))
  with check (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.update')));

-- campaign_leads
drop policy if exists "campaign_leads: tenant access" on public.campaign_leads;
create policy "campaign_leads: access"
  on public.campaign_leads for all to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('campaign.read')))
  with check (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('campaign.update')));

-- call_participants
drop policy if exists "call_participants: tenant read" on public.call_participants;
create policy "call_participants: read"
  on public.call_participants for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('call.read')));

-- call_messages
drop policy if exists "call_messages: tenant read" on public.call_messages;
create policy "call_messages: read"
  on public.call_messages for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('call.read')));

-- call_events
drop policy if exists "call_events: tenant read" on public.call_events;
create policy "call_events: read"
  on public.call_events for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('call.read')));

-- agent_sessions
drop policy if exists "agent_sessions: tenant read" on public.agent_sessions;
create policy "agent_sessions: read"
  on public.agent_sessions for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('call.read')));

-- conversation_summaries
drop policy if exists "conv_summaries: tenant read" on public.conversation_summaries;
create policy "conv_summaries: read"
  on public.conversation_summaries for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('call.read')));

-- lead_score_events
drop policy if exists "lead_score_events: tenant read" on public.lead_score_events;
create policy "lead_score_events: read"
  on public.lead_score_events for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.read')));

-- sales_assignments
drop policy if exists "sales_assignments: tenant access" on public.sales_assignments;
create policy "sales_assignments: access"
  on public.sales_assignments for all to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.read')))
  with check (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.assign')));

-- follow_ups
drop policy if exists "follow_ups: tenant access" on public.follow_ups;
create policy "follow_ups: access"
  on public.follow_ups for all to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('appointment.read')))
  with check (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('appointment.create')));

-- appointment_history
drop policy if exists "appt_history: tenant read" on public.appointment_history;
create policy "appt_history: read"
  on public.appointment_history for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('appointment.read')));

-- whatsapp_templates
drop policy if exists "wa_templates: tenant access" on public.whatsapp_templates;
create policy "wa_templates: access"
  on public.whatsapp_templates for all to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('campaign.read')))
  with check (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('campaign.update')));

-- whatsapp_messages
drop policy if exists "wa_messages: tenant read" on public.whatsapp_messages;
create policy "wa_messages: read"
  on public.whatsapp_messages for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('customer.read')));

-- communication_logs
drop policy if exists "comm_logs: tenant read" on public.communication_logs;
create policy "comm_logs: read"
  on public.communication_logs for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('customer.read')));

-- activities
drop policy if exists "activities: tenant read" on public.activities;
create policy "activities: read"
  on public.activities for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.read')));

-- project images / docs
drop policy if exists "project_images: tenant read" on public.project_images;
create policy "project_images: read"
  on public.project_images for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('project.read')));

drop policy if exists "property_images: tenant read" on public.property_images;
create policy "property_images: read"
  on public.property_images for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.read')));

drop policy if exists "property_docs: tenant read" on public.property_documents;
create policy "property_docs: read"
  on public.property_documents for select to authenticated
  using (public.is_super_admin() or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.read')));

-- ---------------------------------------------------------------------------
-- STEP 6: NOTIFICATIONS — super_admin sees all, users see own
-- Policy was already user_id-scoped, no change needed.
-- Super_admin accessing notifications for support: via service_role only.
-- ---------------------------------------------------------------------------

-- No change to notifications policies (user-level, not tenant-level).

-- ---------------------------------------------------------------------------
-- STEP 7: Update seed data — assign super_admin user with tenant_id = NULL
-- ---------------------------------------------------------------------------

-- NOTE: In the seed data, super_admin user was given tenant_id of tenant A.
-- For actual platform super_admin accounts, tenant_id should be NULL.
-- Update the seed super_admin (if it exists) to have no tenant.
update public.users
set    tenant_id = null
where  email = 'superadmin@platform.com'
  and  exists (
    select 1 from public.users where email = 'superadmin@platform.com'
  );

comment on function public.is_super_admin() is
  'Returns TRUE if the current Supabase JWT user has an active super_admin system role. '
  'super_admin: tenant_id IS NULL, global access to all tenants via RLS bypass. '
  'admin: tenant_id IS NOT NULL, scoped to their tenant only.';
