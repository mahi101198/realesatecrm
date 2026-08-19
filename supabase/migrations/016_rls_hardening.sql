-- =============================================================================
-- MIGRATION 016: RLS Hardening
--
-- Fixes:
-- 1. Analytics views lack tenant filter → expose cross-tenant data
-- 2. users: INSERT policy uses wrong permission code (customer.create)
-- 3. Add user.create and user.update permissions
-- 4. campaigns.agent_config_id FK: SET NULL → RESTRICT
-- 5. webhook_events: UNIQUE allows NULL → partial unique only
-- 6. user_roles: missing explicit UPDATE/DELETE policies
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. ANALYTICS VIEWS — Tenant filter
--    All views in 011 lacked WHERE tenant_id = get_current_tenant_id().
--    Without it, an authenticated user querying the view via a security_definer
--    function could see all tenants. Recreating with explicit tenant filter.
-- ---------------------------------------------------------------------------

create or replace view public.v_lead_pipeline_summary as
select
  l.tenant_id,
  l.status,
  l.sales_stage,
  count(*)                                                 as total_leads,
  count(*) filter (where l.lead_score >= 81)              as very_hot,
  count(*) filter (where l.lead_score between 61 and 80)  as hot,
  count(*) filter (where l.lead_score between 41 and 60)  as warm,
  count(*) filter (where l.lead_score between 21 and 40)  as cold,
  count(*) filter (where l.lead_score <= 20)              as very_cold,
  round(avg(l.lead_score), 1)                             as avg_score,
  max(l.created_at)                                       as last_lead_at
from   public.leads l
where  l.deleted_at is null
  and  l.tenant_id = public.get_current_tenant_id()
group  by l.tenant_id, l.status, l.sales_stage;

comment on view public.v_lead_pipeline_summary is
  'Lead pipeline breakdown by status, stage, and score band. Tenant-filtered.';

create or replace view public.v_call_metrics as
select
  c.tenant_id,
  c.campaign_id,
  date_trunc('day', c.created_at at time zone 'Asia/Kolkata') as call_date,
  count(*)                                                      as total_calls,
  count(*) filter (where c.status = 'completed')               as completed_calls,
  count(*) filter (where c.answered_at is not null)            as answered_calls,
  count(*) filter (where c.status = 'no_answer')               as no_answer_calls,
  count(*) filter (where c.status = 'failed')                  as failed_calls,
  count(*) filter (where c.outcome = 'qualified')              as qualified_calls,
  count(*) filter (where c.outcome = 'site_visit_booked')      as site_visit_booked,
  count(*) filter (where c.outcome = 'human_transfer')         as human_transfers,
  round(avg(c.duration_seconds)::numeric, 1)                   as avg_duration_s,
  round(avg(c.avg_response_latency_ms)::numeric, 0)            as avg_latency_ms,
  sum(c.interruption_count)                                     as total_interruptions,
  sum(c.llm_input_tokens)                                       as total_input_tokens,
  sum(c.llm_output_tokens)                                      as total_output_tokens,
  sum(c.stt_duration_seconds)                                   as total_stt_seconds,
  sum(c.tts_characters)                                         as total_tts_chars
from   public.calls c
where  c.tenant_id = public.get_current_tenant_id()
group  by c.tenant_id, c.campaign_id, call_date;

comment on view public.v_call_metrics is
  'Daily aggregated call performance metrics per campaign. Tenant-filtered.';

create or replace view public.v_property_availability as
select
  p.tenant_id,
  p.project_id,
  proj.name                                                as project_name,
  pt.name                                                  as property_type,
  count(*)                                                 as total_units,
  count(*) filter (where p.status = 'available')          as available,
  count(*) filter (where p.status = 'reserved')           as reserved,
  count(*) filter (where p.status = 'hold')               as on_hold,
  count(*) filter (where p.status = 'sold')               as sold,
  min(p.base_price)                                        as price_min,
  max(p.base_price)                                        as price_max
from   public.properties p
join   public.projects proj      on proj.id = p.project_id
join   public.property_types pt  on pt.id  = p.property_type_id
where  p.deleted_at is null
  and  p.tenant_id = public.get_current_tenant_id()
group  by p.tenant_id, p.project_id, proj.name, pt.name;

comment on view public.v_property_availability is
  'Property availability and pricing summary per project and type. Tenant-filtered.';

create or replace view public.v_sales_agent_performance as
select
  sa.tenant_id,
  sa.id                                                        as sales_agent_id,
  u.name                                                       as agent_name,
  count(distinct l.id)                                         as assigned_leads,
  count(distinct l.id) filter (where l.status = 'converted')  as converted_leads,
  count(distinct c.id)                                         as total_calls,
  count(distinct a.id)                                         as total_appointments,
  count(distinct a.id) filter (where a.status = 'completed')  as completed_visits
from   public.sales_agents sa
join   public.users u            on u.id  = sa.user_id
left join public.leads l         on l.assigned_sales_agent_id = sa.id
                                and l.deleted_at is null
left join public.calls c         on c.assigned_sales_agent_id = sa.id
left join public.appointments a  on a.sales_agent_id = sa.id
where  sa.deleted_at is null
  and  sa.tenant_id = public.get_current_tenant_id()
group  by sa.tenant_id, sa.id, u.name;

comment on view public.v_sales_agent_performance is
  'Sales agent performance summary. Tenant-filtered.';

create or replace view public.v_campaign_performance as
select
  c.tenant_id,
  c.id                                                         as campaign_id,
  c.name                                                       as campaign_name,
  c.status                                                     as campaign_status,
  c.type                                                       as campaign_type,
  count(distinct cl.id)                                        as total_enrolled_leads,
  count(distinct cl.id) filter (where cl.status = 'called')   as called,
  count(distinct cl.id) filter (where cl.status = 'qualified') as qualified,
  count(distinct cl.id) filter (where cl.status = 'not_interested') as not_interested,
  sum(cl.attempt_count)                                        as total_attempts,
  count(distinct calls.id)                                     as total_calls,
  count(distinct calls.id) filter (
    where calls.outcome = 'site_visit_booked'
  )                                                            as site_visits_booked
from   public.campaigns c
left join public.campaign_leads cl  on cl.campaign_id = c.id
left join public.calls calls        on calls.campaign_id = c.id
where  c.deleted_at is null
  and  c.tenant_id = public.get_current_tenant_id()
group  by c.tenant_id, c.id, c.name, c.status, c.type;

comment on view public.v_campaign_performance is
  'Campaign funnel metrics. Tenant-filtered.';

-- Refresh materialized view with tenant filter in data
-- Note: mv_hot_leads stores all tenants' data (correct for a background materialized view)
-- but application code MUST filter by tenant_id when querying it.
-- We add a comment to document this explicitly.
comment on materialized view public.mv_hot_leads is
  'Hot leads across ALL tenants. '
  'ALWAYS filter by tenant_id when querying: WHERE tenant_id = $1. '
  'Refresh: REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_hot_leads;';

-- ---------------------------------------------------------------------------
-- 2. ADD user.create AND user.update PERMISSIONS
--    The INSERT policy for the users table previously used has_permission(''customer.create'')
--    which is a CRM permission, not a user-management permission.
-- ---------------------------------------------------------------------------

insert into public.permissions (code, name, description, resource, action) values
  ('user.create', 'Create Users',   'Invite and create CRM users.',       'user', 'create'),
  ('user.update', 'Update Users',   'Update CRM user profiles and roles.', 'user', 'update')
on conflict (code) do nothing;

-- Grant to admin and super_admin (system roles have no tenant_id)
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r
join   public.permissions p on p.code in ('user.create', 'user.update')
where  r.name in ('super_admin', 'admin')
  and  r.is_system_role = true
on conflict do nothing;

-- Fix the INSERT policy
drop policy if exists "users: admins can insert" on public.users;

create policy "users: admins can insert"
  on public.users for insert
  to authenticated
  with check (
    tenant_id = public.get_current_tenant_id()
    and public.has_permission('user.create')
  );

-- Fix the UPDATE policy to use user.update
drop policy if exists "users: admins can update" on public.users;

create policy "users: admins can update"
  on public.users for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (
    tenant_id = public.get_current_tenant_id()
    and public.has_permission('user.update')
  );

-- ---------------------------------------------------------------------------
-- 3. CAMPAIGNS: Fix agent_config_id FK to RESTRICT
--    Currently ON DELETE SET NULL — this silently orphans the campaign's AI
--    configuration without warning. RESTRICT prevents accidental deletion of
--    a config that is still referenced by an active campaign.
-- ---------------------------------------------------------------------------

alter table public.campaigns
  drop constraint if exists fk_campaigns_agent_config;

alter table public.campaigns
  add constraint fk_campaigns_agent_config
  foreign key (agent_config_id)
  references public.agent_configs (id)
  on delete restrict;

comment on constraint fk_campaigns_agent_config on public.campaigns is
  'Prevents deletion of an agent_config that is still referenced by a campaign. '
  'Soft-delete (deleted_at) the config instead.';

-- ---------------------------------------------------------------------------
-- 4. WEBHOOK_EVENTS: Fix NULL idempotency
--    The original UNIQUE (provider, external_event_id) uses DEFERRABLE but
--    PostgreSQL treats NULL != NULL in unique constraints, so two rows with
--    external_event_id = NULL from the same provider both insert successfully.
--    Replace with a partial unique index (only unique when not null).
-- ---------------------------------------------------------------------------

alter table public.webhook_events
  drop constraint if exists uq_webhook_events_provider_external;

create unique index if not exists uq_webhook_events_provider_external_notnull
  on public.webhook_events (provider, external_event_id)
  where external_event_id is not null;

comment on index public.uq_webhook_events_provider_external_notnull is
  'Prevents duplicate processing of webhook events that have a provider event ID. '
  'Events without an ID (NULL) are not DB-deduplicated — FastAPI handles those.';

-- ---------------------------------------------------------------------------
-- 5. USER_ROLES: Add explicit UPDATE and DELETE policies
--    The existing "for all" policy is correct but adding explicit policies
--    makes intent clear and allows future granular adjustment.
-- ---------------------------------------------------------------------------

drop policy if exists "user_roles: admins can manage" on public.user_roles;

create policy "user_roles: admins can insert"
  on public.user_roles for insert
  to authenticated
  with check (
    tenant_id = public.get_current_tenant_id()
    and public.has_permission('user.update')
  );

create policy "user_roles: admins can update"
  on public.user_roles for update
  to authenticated
  using (tenant_id = public.get_current_tenant_id())
  with check (
    tenant_id = public.get_current_tenant_id()
    and public.has_permission('user.update')
  );

create policy "user_roles: admins can delete"
  on public.user_roles for delete
  to authenticated
  using (
    tenant_id = public.get_current_tenant_id()
    and public.has_permission('user.update')
  );
