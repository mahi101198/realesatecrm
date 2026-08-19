-- =============================================================================
-- MIGRATION 011: Analytics Views
-- Derives operational metrics from normalized data without extra tables.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- VIEW: v_lead_pipeline_summary
-- Per-tenant lead count grouped by status and stage.
-- ---------------------------------------------------------------------------

create or replace view public.v_lead_pipeline_summary as
select
  l.tenant_id,
  l.status,
  l.sales_stage,
  count(*)                                               as total_leads,
  count(*) filter (where l.lead_score >= 81)            as very_hot,
  count(*) filter (where l.lead_score between 61 and 80) as hot,
  count(*) filter (where l.lead_score between 41 and 60) as warm,
  count(*) filter (where l.lead_score between 21 and 40) as cold,
  count(*) filter (where l.lead_score <= 20)            as very_cold,
  avg(l.lead_score)                                     as avg_score,
  max(l.created_at)                                     as last_lead_at
from   public.leads l
where  l.deleted_at is null
group  by l.tenant_id, l.status, l.sales_stage;

comment on view public.v_lead_pipeline_summary is
  'Lead pipeline breakdown by status, stage, and score band.';

-- ---------------------------------------------------------------------------
-- VIEW: v_call_metrics
-- Per-tenant call performance metrics.
-- ---------------------------------------------------------------------------

create or replace view public.v_call_metrics as
select
  c.tenant_id,
  c.campaign_id,
  date_trunc('day', c.created_at at time zone 'Asia/Kolkata') as call_date,
  count(*)                                                     as total_calls,
  count(*) filter (where c.status = 'completed')              as completed_calls,
  count(*) filter (where c.answered_at is not null)           as answered_calls,
  count(*) filter (where c.status = 'no_answer')              as no_answer_calls,
  count(*) filter (where c.status = 'failed')                 as failed_calls,
  count(*) filter (where c.outcome = 'qualified')             as qualified_calls,
  count(*) filter (where c.outcome = 'site_visit_booked')     as site_visit_booked,
  count(*) filter (where c.outcome = 'human_transfer')        as human_transfers,
  avg(c.duration_seconds)                                     as avg_duration_s,
  avg(c.avg_response_latency_ms)                             as avg_latency_ms,
  sum(c.interruption_count)                                   as total_interruptions,
  sum(c.llm_input_tokens)                                     as total_input_tokens,
  sum(c.llm_output_tokens)                                    as total_output_tokens,
  sum(c.stt_duration_seconds)                                 as total_stt_seconds,
  sum(c.tts_characters)                                       as total_tts_chars
from   public.calls c
group  by c.tenant_id, c.campaign_id, call_date;

comment on view public.v_call_metrics is
  'Daily aggregated call performance and AI usage metrics per campaign.';

-- ---------------------------------------------------------------------------
-- VIEW: v_property_availability
-- Quick availability summary per project.
-- ---------------------------------------------------------------------------

create or replace view public.v_property_availability as
select
  p.tenant_id,
  p.project_id,
  proj.name                                           as project_name,
  pt.name                                             as property_type,
  count(*)                                            as total_units,
  count(*) filter (where p.status = 'available')     as available,
  count(*) filter (where p.status = 'reserved')      as reserved,
  count(*) filter (where p.status = 'hold')          as on_hold,
  count(*) filter (where p.status = 'sold')          as sold,
  min(p.base_price)                                  as price_min,
  max(p.base_price)                                  as price_max
from   public.properties p
join   public.projects proj on proj.id = p.project_id
join   public.property_types pt on pt.id = p.property_type_id
where  p.deleted_at is null
group  by p.tenant_id, p.project_id, proj.name, pt.name;

comment on view public.v_property_availability is
  'Property availability and pricing summary per project per type.';

-- ---------------------------------------------------------------------------
-- VIEW: v_sales_agent_performance
-- Per-agent lead and call performance.
-- ---------------------------------------------------------------------------

create or replace view public.v_sales_agent_performance as
select
  sa.tenant_id,
  sa.id                                               as sales_agent_id,
  u.name                                              as agent_name,
  count(distinct l.id)                                as assigned_leads,
  count(distinct l.id) filter (where l.status = 'converted') as converted_leads,
  count(distinct c.id)                                as total_calls,
  count(distinct a.id)                                as total_appointments,
  count(distinct a.id) filter (where a.status = 'completed') as completed_visits
from   public.sales_agents sa
join   public.users u       on u.id = sa.user_id
left join public.leads l    on l.assigned_sales_agent_id = sa.id and l.deleted_at is null
left join public.calls c    on c.assigned_sales_agent_id = sa.id
left join public.appointments a on a.sales_agent_id = sa.id
where  sa.deleted_at is null
group  by sa.tenant_id, sa.id, u.name;

comment on view public.v_sales_agent_performance is
  'Sales agent performance summary: leads, calls, appointments, conversions.';

-- ---------------------------------------------------------------------------
-- VIEW: v_campaign_performance
-- Campaign-level funnel metrics.
-- ---------------------------------------------------------------------------

create or replace view public.v_campaign_performance as
select
  c.tenant_id,
  c.id                                                as campaign_id,
  c.name                                              as campaign_name,
  c.status                                            as campaign_status,
  c.type                                              as campaign_type,
  count(distinct cl.id)                               as total_enrolled_leads,
  count(distinct cl.id) filter (where cl.status = 'called')      as called,
  count(distinct cl.id) filter (where cl.status = 'qualified')   as qualified,
  count(distinct cl.id) filter (where cl.status = 'not_interested') as not_interested,
  sum(cl.attempt_count)                               as total_attempts,
  count(distinct calls.id)                            as total_calls,
  count(distinct calls.id) filter (where calls.outcome = 'site_visit_booked') as site_visits_booked
from   public.campaigns c
left join public.campaign_leads cl on cl.campaign_id = c.id
left join public.calls calls       on calls.campaign_id = c.id
where  c.deleted_at is null
group  by c.tenant_id, c.id, c.name, c.status, c.type;

comment on view public.v_campaign_performance is
  'Campaign funnel: enrolled leads, calls, qualifications, site visits.';

-- ---------------------------------------------------------------------------
-- MATERIALIZED VIEW: mv_hot_leads
-- Refreshed periodically (cron/FastAPI scheduler).
-- Identifies leads with score >= 61 needing follow-up.
-- ---------------------------------------------------------------------------

create materialized view if not exists public.mv_hot_leads as
select
  l.tenant_id,
  l.id                          as lead_id,
  l.lead_number,
  c.full_name                   as customer_name,
  c.phone,
  l.lead_score,
  l.status,
  l.sales_stage,
  l.next_follow_up_at,
  l.assigned_sales_agent_id,
  l.last_contacted_at,
  l.updated_at
from   public.leads l
join   public.customers c on c.id = l.customer_id
where  l.lead_score >= 61
  and  l.status in ('new', 'active')
  and  l.deleted_at is null
order  by l.lead_score desc, l.next_follow_up_at asc;

create unique index on public.mv_hot_leads (tenant_id, lead_id);
create index        on public.mv_hot_leads (tenant_id, lead_score desc);
create index        on public.mv_hot_leads (tenant_id, next_follow_up_at);

comment on materialized view public.mv_hot_leads is
  'Hot leads requiring follow-up. Refresh via: REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_hot_leads;';
