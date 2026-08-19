-- =============================================================================
-- MIGRATION 010: Row Level Security Policies
-- Enables RLS on every tenant-owned table and creates appropriate policies.
-- =============================================================================
-- SECURITY MODEL:
--   1. Authenticated Supabase users can only access their own tenant's data.
--   2. Service role (FastAPI backend) bypasses RLS entirely.
--   3. super_admin users (no tenant_id) can read all tenants.
--   4. Immutable tables (audit_logs, activities, etc.) have INSERT-only
--      policies for normal users; DELETE and UPDATE are denied.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- HELPER VIEWS (used inside policies)
-- ---------------------------------------------------------------------------

-- Check if the current user has a specific permission
create or replace function public.has_permission(p_permission_code text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from   public.user_roles ur
    join   public.role_permissions rp on rp.role_id = ur.role_id
    join   public.permissions p       on p.id = rp.permission_id
    where  ur.user_id = (select id from public.users where auth_user_id = auth.uid() limit 1)
      and  p.code     = p_permission_code
      and  ur.is_active = true
      and  (ur.expires_at is null or ur.expires_at > now())
  );
$$;

comment on function public.has_permission(text) is
  'Returns true if the current Supabase user has the given permission code.';

-- ---------------------------------------------------------------------------
-- TENANTS
-- ---------------------------------------------------------------------------

alter table public.tenants enable row level security;

create policy "tenants: service_role full access"
  on public.tenants for all
  to service_role using (true) with check (true);

create policy "tenants: users see own tenant"
  on public.tenants for select
  to authenticated
  using (id = public.get_current_tenant_id());

-- ---------------------------------------------------------------------------
-- USERS
-- ---------------------------------------------------------------------------

alter table public.users enable row level security;

create policy "users: service_role full access"
  on public.users for all
  to service_role using (true) with check (true);

create policy "users: tenant members can read"
  on public.users for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id());

create policy "users: admins can insert"
  on public.users for insert
  to authenticated
  with check (
    tenant_id = public.get_current_tenant_id()
    and public.has_permission('customer.create')  -- reuse; any admin-level perm
  );

create policy "users: admins can update"
  on public.users for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id());

-- ---------------------------------------------------------------------------
-- ROLES / PERMISSIONS / ROLE_PERMISSIONS / USER_ROLES
-- ---------------------------------------------------------------------------

-- Roles: readable by all authenticated (needed to show permission picker in UI)
alter table public.roles enable row level security;

create policy "roles: service_role full access"
  on public.roles for all
  to service_role using (true) with check (true);

create policy "roles: authenticated can read system or own tenant roles"
  on public.roles for select
  to authenticated
  using (tenant_id is null or tenant_id = public.get_current_tenant_id());

-- Permissions: read-only for authenticated
alter table public.permissions enable row level security;

create policy "permissions: service_role full access"
  on public.permissions for all
  to service_role using (true) with check (true);

create policy "permissions: authenticated can read"
  on public.permissions for select
  to authenticated
  using (true);

-- role_permissions: read-only for authenticated
alter table public.role_permissions enable row level security;

create policy "role_permissions: service_role full access"
  on public.role_permissions for all
  to service_role using (true) with check (true);

create policy "role_permissions: authenticated can read"
  on public.role_permissions for select
  to authenticated
  using (true);

-- user_roles: tenant-scoped
alter table public.user_roles enable row level security;

create policy "user_roles: service_role full access"
  on public.user_roles for all
  to service_role using (true) with check (true);

create policy "user_roles: tenant can read"
  on public.user_roles for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id());

create policy "user_roles: admins can manage"
  on public.user_roles for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id());

-- ---------------------------------------------------------------------------
-- SALES_AGENTS
-- ---------------------------------------------------------------------------

alter table public.sales_agents enable row level security;

create policy "sales_agents: service_role full access"
  on public.sales_agents for all
  to service_role using (true) with check (true);

create policy "sales_agents: tenant read"
  on public.sales_agents for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('sales_agent.read'));

create policy "sales_agents: managers can insert/update"
  on public.sales_agents for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('sales_agent.assign'));

create policy "sales_agents: managers can update"
  on public.sales_agents for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id());

-- ---------------------------------------------------------------------------
-- PROJECTS
-- ---------------------------------------------------------------------------

alter table public.projects enable row level security;

create policy "projects: service_role full access"
  on public.projects for all
  to service_role using (true) with check (true);

create policy "projects: tenant read"
  on public.projects for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('project.read')
         and deleted_at is null);

create policy "projects: authorized can insert"
  on public.projects for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('project.create'));

create policy "projects: authorized can update"
  on public.projects for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('project.update'));

-- ---------------------------------------------------------------------------
-- PROPERTIES
-- ---------------------------------------------------------------------------

alter table public.properties enable row level security;

create policy "properties: service_role full access"
  on public.properties for all
  to service_role using (true) with check (true);

create policy "properties: tenant read"
  on public.properties for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('property.read')
         and deleted_at is null);

create policy "properties: authorized can insert"
  on public.properties for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('property.create'));

create policy "properties: authorized can update"
  on public.properties for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('property.update'));

create policy "properties: admins can delete"
  on public.properties for delete
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('property.delete'));

-- ---------------------------------------------------------------------------
-- PROPERTY_STATUS_HISTORY (append-only)
-- ---------------------------------------------------------------------------

alter table public.property_status_history enable row level security;

create policy "prop_status_hist: service_role full access"
  on public.property_status_history for all
  to service_role using (true) with check (true);

create policy "prop_status_hist: tenant read"
  on public.property_status_history for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('property.read'));

-- INSERT allowed for authenticated (system trigger driven)
create policy "prop_status_hist: system can insert"
  on public.property_status_history for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id());

-- NO UPDATE / DELETE policy = immutable for normal users

-- ---------------------------------------------------------------------------
-- PROPERTY_PRICES
-- ---------------------------------------------------------------------------

alter table public.property_prices enable row level security;

create policy "property_prices: service_role full access"
  on public.property_prices for all
  to service_role using (true) with check (true);

create policy "property_prices: tenant read"
  on public.property_prices for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('property.read'));

create policy "property_prices: authorized can insert/update"
  on public.property_prices for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('property.update'));

create policy "property_prices: authorized can update"
  on public.property_prices for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('property.update'));

-- ---------------------------------------------------------------------------
-- PROPERTY_PRICE_HISTORY (append-only)
-- ---------------------------------------------------------------------------

alter table public.property_price_history enable row level security;

create policy "price_history: service_role full access"
  on public.property_price_history for all
  to service_role using (true) with check (true);

create policy "price_history: tenant read"
  on public.property_price_history for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('property.read'));

create policy "price_history: system can insert"
  on public.property_price_history for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id());

-- ---------------------------------------------------------------------------
-- CUSTOMERS
-- ---------------------------------------------------------------------------

alter table public.customers enable row level security;

create policy "customers: service_role full access"
  on public.customers for all
  to service_role using (true) with check (true);

create policy "customers: tenant read"
  on public.customers for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('customer.read')
         and deleted_at is null);

create policy "customers: authorized can insert"
  on public.customers for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('customer.create'));

create policy "customers: authorized can update"
  on public.customers for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('customer.update'));

-- ---------------------------------------------------------------------------
-- CUSTOMER_PREFERENCES / CUSTOMER_NOTES
-- ---------------------------------------------------------------------------

alter table public.customer_preferences enable row level security;

create policy "customer_prefs: service_role full access"
  on public.customer_preferences for all
  to service_role using (true) with check (true);

create policy "customer_prefs: tenant access"
  on public.customer_preferences for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('customer.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('customer.update'));

alter table public.customer_notes enable row level security;

create policy "customer_notes: service_role full access"
  on public.customer_notes for all
  to service_role using (true) with check (true);

create policy "customer_notes: tenant access"
  on public.customer_notes for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('customer.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('customer.update'));

-- ---------------------------------------------------------------------------
-- LEADS
-- ---------------------------------------------------------------------------

alter table public.leads enable row level security;

create policy "leads: service_role full access"
  on public.leads for all
  to service_role using (true) with check (true);

create policy "leads: tenant read"
  on public.leads for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('lead.read')
         and deleted_at is null);

create policy "leads: authorized can insert"
  on public.leads for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('lead.create'));

create policy "leads: authorized can update"
  on public.leads for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('lead.update'));

-- ---------------------------------------------------------------------------
-- LEAD_PROPERTY_INTERESTS / LEAD_NOTES
-- ---------------------------------------------------------------------------

alter table public.lead_property_interests enable row level security;

create policy "lead_prop_interests: service_role full access"
  on public.lead_property_interests for all
  to service_role using (true) with check (true);

create policy "lead_prop_interests: tenant access"
  on public.lead_property_interests for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('lead.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('lead.update'));

alter table public.lead_notes enable row level security;

create policy "lead_notes: service_role full access"
  on public.lead_notes for all
  to service_role using (true) with check (true);

create policy "lead_notes: tenant access"
  on public.lead_notes for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('lead.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('lead.update'));

-- ---------------------------------------------------------------------------
-- CAMPAIGNS / CAMPAIGN_LEADS
-- ---------------------------------------------------------------------------

alter table public.campaigns enable row level security;

create policy "campaigns: service_role full access"
  on public.campaigns for all
  to service_role using (true) with check (true);

create policy "campaigns: tenant read"
  on public.campaigns for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('campaign.read')
         and deleted_at is null);

create policy "campaigns: authorized can insert"
  on public.campaigns for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('campaign.create'));

create policy "campaigns: authorized can update"
  on public.campaigns for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('campaign.update'));

alter table public.campaign_leads enable row level security;

create policy "campaign_leads: service_role full access"
  on public.campaign_leads for all
  to service_role using (true) with check (true);

create policy "campaign_leads: tenant access"
  on public.campaign_leads for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('campaign.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('campaign.update'));

-- ---------------------------------------------------------------------------
-- AGENT_CONFIGS
-- ---------------------------------------------------------------------------

alter table public.agent_configs enable row level security;

create policy "agent_configs: service_role full access"
  on public.agent_configs for all
  to service_role using (true) with check (true);

create policy "agent_configs: tenant read"
  on public.agent_configs for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('agent_config.read')
         and deleted_at is null);

create policy "agent_configs: authorized can insert/update"
  on public.agent_configs for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('agent_config.update'));

create policy "agent_configs: authorized can update"
  on public.agent_configs for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('agent_config.update'));

-- ---------------------------------------------------------------------------
-- CALLS (read-only for UI users)
-- ---------------------------------------------------------------------------

alter table public.calls enable row level security;

create policy "calls: service_role full access"
  on public.calls for all
  to service_role using (true) with check (true);

create policy "calls: tenant read"
  on public.calls for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('call.read'));

-- INSERT/UPDATE only via service_role (FastAPI)

-- ---------------------------------------------------------------------------
-- CALL_PARTICIPANTS / CALL_MESSAGES / CALL_EVENTS (read-only for UI)
-- ---------------------------------------------------------------------------

alter table public.call_participants enable row level security;

create policy "call_participants: service_role full access"
  on public.call_participants for all
  to service_role using (true) with check (true);

create policy "call_participants: tenant read"
  on public.call_participants for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('call.read'));

alter table public.call_messages enable row level security;

create policy "call_messages: service_role full access"
  on public.call_messages for all
  to service_role using (true) with check (true);

create policy "call_messages: tenant read"
  on public.call_messages for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('call.read'));

alter table public.call_events enable row level security;

create policy "call_events: service_role full access"
  on public.call_events for all
  to service_role using (true) with check (true);

create policy "call_events: tenant read"
  on public.call_events for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('call.read'));

-- ---------------------------------------------------------------------------
-- AGENT_SESSIONS / CONVERSATION_SUMMARIES / LEAD_SCORE_EVENTS
-- ---------------------------------------------------------------------------

alter table public.agent_sessions enable row level security;

create policy "agent_sessions: service_role full access"
  on public.agent_sessions for all
  to service_role using (true) with check (true);

create policy "agent_sessions: tenant read"
  on public.agent_sessions for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('call.read'));

alter table public.conversation_summaries enable row level security;

create policy "conv_summaries: service_role full access"
  on public.conversation_summaries for all
  to service_role using (true) with check (true);

create policy "conv_summaries: tenant read"
  on public.conversation_summaries for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('call.read'));

alter table public.lead_score_events enable row level security;

create policy "lead_score_events: service_role full access"
  on public.lead_score_events for all
  to service_role using (true) with check (true);

create policy "lead_score_events: tenant read"
  on public.lead_score_events for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('lead.read'));

-- ---------------------------------------------------------------------------
-- SALES_ASSIGNMENTS / FOLLOW_UPS / APPOINTMENTS / APPOINTMENT_HISTORY
-- ---------------------------------------------------------------------------

alter table public.sales_assignments enable row level security;

create policy "sales_assignments: service_role full access"
  on public.sales_assignments for all
  to service_role using (true) with check (true);

create policy "sales_assignments: tenant access"
  on public.sales_assignments for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('lead.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('lead.assign'));

alter table public.follow_ups enable row level security;

create policy "follow_ups: service_role full access"
  on public.follow_ups for all
  to service_role using (true) with check (true);

create policy "follow_ups: tenant access"
  on public.follow_ups for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('appointment.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('appointment.create'));

alter table public.appointments enable row level security;

create policy "appointments: service_role full access"
  on public.appointments for all
  to service_role using (true) with check (true);

create policy "appointments: tenant read"
  on public.appointments for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('appointment.read'));

create policy "appointments: authorized can insert"
  on public.appointments for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('appointment.create'));

create policy "appointments: authorized can update"
  on public.appointments for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('appointment.update'));

alter table public.appointment_history enable row level security;

create policy "appt_history: service_role full access"
  on public.appointment_history for all
  to service_role using (true) with check (true);

create policy "appt_history: tenant read"
  on public.appointment_history for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('appointment.read'));

create policy "appt_history: system can insert"
  on public.appointment_history for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id());

-- ---------------------------------------------------------------------------
-- WHATSAPP_TEMPLATES / WHATSAPP_MESSAGES / COMMUNICATION_LOGS
-- ---------------------------------------------------------------------------

alter table public.whatsapp_templates enable row level security;

create policy "wa_templates: service_role full access"
  on public.whatsapp_templates for all
  to service_role using (true) with check (true);

create policy "wa_templates: tenant access"
  on public.whatsapp_templates for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('campaign.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('campaign.update'));

alter table public.whatsapp_messages enable row level security;

create policy "wa_messages: service_role full access"
  on public.whatsapp_messages for all
  to service_role using (true) with check (true);

create policy "wa_messages: tenant read"
  on public.whatsapp_messages for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('customer.read'));

alter table public.communication_logs enable row level security;

create policy "comm_logs: service_role full access"
  on public.communication_logs for all
  to service_role using (true) with check (true);

create policy "comm_logs: tenant read"
  on public.communication_logs for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('customer.read'));

-- ---------------------------------------------------------------------------
-- ACTIVITIES (append-only)
-- ---------------------------------------------------------------------------

alter table public.activities enable row level security;

create policy "activities: service_role full access"
  on public.activities for all
  to service_role using (true) with check (true);

create policy "activities: tenant read"
  on public.activities for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('lead.read'));

create policy "activities: system can insert"
  on public.activities for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id());

-- ---------------------------------------------------------------------------
-- NOTIFICATIONS
-- ---------------------------------------------------------------------------

alter table public.notifications enable row level security;

create policy "notifications: service_role full access"
  on public.notifications for all
  to service_role using (true) with check (true);

-- Users can only see their own notifications
create policy "notifications: user sees own"
  on public.notifications for select
  to authenticated
  using (user_id = (select id from public.users where auth_user_id = auth.uid() limit 1));

create policy "notifications: user can update own (mark read)"
  on public.notifications for update
  to authenticated
  using (user_id = (select id from public.users where auth_user_id = auth.uid() limit 1))
  with check (user_id = (select id from public.users where auth_user_id = auth.uid() limit 1));

-- ---------------------------------------------------------------------------
-- AUDIT_LOGS (append-only, restricted read)
-- ---------------------------------------------------------------------------

alter table public.audit_logs enable row level security;

create policy "audit_logs: service_role full access"
  on public.audit_logs for all
  to service_role using (true) with check (true);

create policy "audit_logs: authorized tenant read"
  on public.audit_logs for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('audit.read'));

create policy "audit_logs: system can insert"
  on public.audit_logs for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id());

-- ---------------------------------------------------------------------------
-- INTEGRATIONS
-- ---------------------------------------------------------------------------

alter table public.integrations enable row level security;

create policy "integrations: service_role full access"
  on public.integrations for all
  to service_role using (true) with check (true);

create policy "integrations: admins can manage"
  on public.integrations for all
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('agent_config.read'))
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('agent_config.update'));

-- ---------------------------------------------------------------------------
-- WEBHOOK_EVENTS
-- ---------------------------------------------------------------------------

alter table public.webhook_events enable row level security;

create policy "webhook_events: service_role full access"
  on public.webhook_events for all
  to service_role using (true) with check (true);

create policy "webhook_events: admins can read"
  on public.webhook_events for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('audit.read'));

-- ---------------------------------------------------------------------------
-- ALSO ENABLE RLS ON LOOKUP / SHARED TABLES
-- (Platform-wide tables need safe read policies)
-- ---------------------------------------------------------------------------

alter table public.property_types enable row level security;

create policy "property_types: all authenticated can read"
  on public.property_types for select
  to authenticated
  using (is_active = true);

create policy "property_types: service_role full access"
  on public.property_types for all
  to service_role using (true) with check (true);

alter table public.project_types enable row level security;

create policy "project_types: all authenticated can read"
  on public.project_types for select
  to authenticated
  using (is_active = true);

create policy "project_types: service_role full access"
  on public.project_types for all
  to service_role using (true) with check (true);

alter table public.amenities enable row level security;

create policy "amenities: all authenticated can read"
  on public.amenities for select
  to authenticated
  using (is_active = true);

create policy "amenities: service_role full access"
  on public.amenities for all
  to service_role using (true) with check (true);

alter table public.lead_sources enable row level security;

create policy "lead_sources: tenant or system can read"
  on public.lead_sources for select
  to authenticated
  using (tenant_id is null or tenant_id = public.get_current_tenant_id());

create policy "lead_sources: service_role full access"
  on public.lead_sources for all
  to service_role using (true) with check (true);

-- Project / property images and documents
alter table public.project_images enable row level security;

create policy "project_images: service_role full access"
  on public.project_images for all
  to service_role using (true) with check (true);

create policy "project_images: tenant read"
  on public.project_images for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('project.read'));

create policy "project_images: authorized insert"
  on public.project_images for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('project.update'));

alter table public.property_images enable row level security;

create policy "property_images: service_role full access"
  on public.property_images for all
  to service_role using (true) with check (true);

create policy "property_images: tenant read"
  on public.property_images for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('property.read'));

create policy "property_images: authorized insert"
  on public.property_images for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('property.update'));

alter table public.property_documents enable row level security;

create policy "property_docs: service_role full access"
  on public.property_documents for all
  to service_role using (true) with check (true);

create policy "property_docs: tenant read"
  on public.property_documents for select
  to authenticated
  using (tenant_id = public.get_current_tenant_id()
         and public.has_permission('property.read'));

create policy "property_docs: authorized insert"
  on public.property_documents for insert
  to authenticated
  with check (tenant_id = public.get_current_tenant_id()
              and public.has_permission('property.update'));

-- Junction tables for amenities (no tenant_id, scoped via parent)
alter table public.project_amenities enable row level security;

create policy "project_amenities: service_role full access"
  on public.project_amenities for all
  to service_role using (true) with check (true);

create policy "project_amenities: authenticated read"
  on public.project_amenities for select
  to authenticated
  using (exists (
    select 1 from public.projects p
    where p.id = project_id
      and p.tenant_id = public.get_current_tenant_id()
  ));

alter table public.property_amenities enable row level security;

create policy "property_amenities: service_role full access"
  on public.property_amenities for all
  to service_role using (true) with check (true);

create policy "property_amenities: authenticated read"
  on public.property_amenities for select
  to authenticated
  using (exists (
    select 1 from public.properties pr
    where pr.id = property_id
      and pr.tenant_id = public.get_current_tenant_id()
  ));
