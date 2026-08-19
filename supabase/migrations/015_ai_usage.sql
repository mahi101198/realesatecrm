-- =============================================================================
-- MIGRATION 015: AI Usage and Cost Tracking
--
-- The existing calls table stores aggregate token counts but provides no
-- granular per-event cost tracking and no cost computation capability.
--
-- This migration adds:
-- 1. ai_service_type enum
-- 2. ai_usage_events table (per-event granular billing data)
-- 3. RLS on ai_usage_events
-- 4. v_call_cost_summary view
-- 5. v_campaign_cost_summary view
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. ENUM: ai_service_type
-- ---------------------------------------------------------------------------

create type public.ai_service_type as enum (
  'telephony',   -- Voice call minutes (Supaphone)
  'stt',         -- Speech-to-text (Deepgram)
  'llm',         -- Large language model (Gemma, etc.)
  'tts',         -- Text-to-speech (Cartesia)
  'other'        -- Future provider types
);

-- ---------------------------------------------------------------------------
-- 2. TABLE: ai_usage_events
--    One row per billable event during a call.
--    Examples:
--      - 1 row per STT segment (Deepgram charges per second)
--      - 1 row per LLM inference (Gemma charges per token)
--      - 1 row per TTS synthesis (Cartesia charges per character)
--      - 1 row for full call telephony (Supaphone charges per minute)
--    All monetary values use numeric (exact), never float.
-- ---------------------------------------------------------------------------

create table public.ai_usage_events (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  call_id               uuid not null references public.calls (id) on delete cascade,
  agent_session_id      uuid references public.agent_sessions (id) on delete set null,
  -- Provider and service identification
  provider              text not null,
    -- 'supaphone' | 'deepgram' | 'google' | 'cartesia' | future providers
  service_type          public.ai_service_type not null,
  model                 text,
    -- 'nova-3' | 'gemma-4-31b-it' | 'sonic-3' | null for telephony
  -- Usage units (interpretation depends on service_type)
  input_units           numeric(18, 6),
    -- LLM: input tokens | STT: seconds | TTS: characters | Telephony: seconds
  output_units          numeric(18, 6),
    -- LLM: output tokens | others: null
  duration_seconds      numeric(12, 3),
    -- Wall-clock duration for the event (if applicable)
  -- Cost (exact numeric, never float)
  unit_cost             numeric(20, 10),
    -- Price per unit at the time of the call (e.g. 0.0000027 per token)
  total_cost            numeric(20, 10),
    -- Computed: (input_units + output_units) * unit_cost or custom formula
  currency              text not null default 'USD',
  -- Raw billing metadata from provider
  metadata              jsonb not null default '{}',
    -- Full raw billing/usage JSON from provider response
  -- Timestamp
  created_at            timestamptz not null default now(),

  constraint chk_ai_usage_cost    check (total_cost is null or total_cost >= 0),
  constraint chk_ai_usage_units   check (input_units is null or input_units >= 0),
  constraint chk_ai_usage_output  check (output_units is null or output_units >= 0)
);

create index idx_ai_usage_call
  on public.ai_usage_events (call_id, created_at);

create index idx_ai_usage_tenant_date
  on public.ai_usage_events (tenant_id, created_at desc);

create index idx_ai_usage_service
  on public.ai_usage_events (tenant_id, service_type, created_at desc);

create index idx_ai_usage_provider
  on public.ai_usage_events (tenant_id, provider, created_at desc);

create index idx_ai_usage_call_service
  on public.ai_usage_events (call_id, service_type);

comment on table public.ai_usage_events is
  'Granular per-event AI and telephony cost tracking. '
  'One row per billable event (STT segment, LLM inference, TTS synthesis, call). '
  'Supports cost-per-call, cost-per-lead, cost-per-site-visit analytics. '
  'Provider/model flexible: new providers require no schema change.';

-- ---------------------------------------------------------------------------
-- 3. RLS on ai_usage_events
-- ---------------------------------------------------------------------------

alter table public.ai_usage_events enable row level security;

create policy "ai_usage: service_role full access"
  on public.ai_usage_events for all
  to service_role
  using (true)
  with check (true);

create policy "ai_usage: tenant analytics read"
  on public.ai_usage_events for select
  to authenticated
  using (
    tenant_id = public.get_current_tenant_id()
    and public.has_permission('analytics.read')
  );

-- INSERT only via service_role (FastAPI populates from provider billing APIs)

-- ---------------------------------------------------------------------------
-- 4. VIEW: v_call_cost_summary
--    Per-call cost breakdown by service type.
-- ---------------------------------------------------------------------------

create or replace view public.v_call_cost_summary as
select
  u.tenant_id,
  u.call_id,
  c.lead_id,
  c.customer_id,
  c.campaign_id,
  c.direction,
  c.status                                                    as call_status,
  c.outcome                                                   as call_outcome,
  c.duration_seconds,
  -- Cost by service type
  coalesce(sum(u.total_cost) filter (where u.service_type = 'telephony'), 0) as telephony_cost,
  coalesce(sum(u.total_cost) filter (where u.service_type = 'stt'),       0) as stt_cost,
  coalesce(sum(u.total_cost) filter (where u.service_type = 'llm'),       0) as llm_cost,
  coalesce(sum(u.total_cost) filter (where u.service_type = 'tts'),       0) as tts_cost,
  coalesce(sum(u.total_cost) filter (where u.service_type = 'other'),     0) as other_cost,
  coalesce(sum(u.total_cost),                                              0) as total_cost,
  max(u.currency)                                             as currency,
  -- Usage summary
  sum(u.input_units)  filter (where u.service_type = 'llm')  as total_llm_input_tokens,
  sum(u.output_units) filter (where u.service_type = 'llm')  as total_llm_output_tokens,
  sum(u.input_units)  filter (where u.service_type = 'stt')  as total_stt_seconds,
  sum(u.input_units)  filter (where u.service_type = 'tts')  as total_tts_characters
from   public.ai_usage_events u
join   public.calls c on c.id = u.call_id
where  u.tenant_id = public.get_current_tenant_id()
group  by u.tenant_id, u.call_id, c.lead_id, c.customer_id, c.campaign_id,
          c.direction, c.status, c.outcome, c.duration_seconds;

comment on view public.v_call_cost_summary is
  'Per-call cost breakdown by service type. Tenant-filtered.';

-- ---------------------------------------------------------------------------
-- 5. VIEW: v_campaign_cost_summary
--    Campaign-level cost and funnel efficiency analytics.
--    Answers: "What is our cost per qualified lead for this campaign?"
-- ---------------------------------------------------------------------------

create or replace view public.v_campaign_cost_summary as
select
  c.tenant_id,
  c.id                                                        as campaign_id,
  c.name                                                      as campaign_name,
  count(distinct calls.id)                                    as total_calls,
  count(distinct calls.id) filter (
    where calls.status = 'completed'
  )                                                           as completed_calls,
  count(distinct calls.id) filter (
    where calls.outcome = 'qualified'
  )                                                           as qualified_calls,
  count(distinct calls.id) filter (
    where calls.outcome = 'site_visit_booked'
  )                                                           as site_visit_calls,
  -- Cost totals
  coalesce(sum(u.total_cost), 0)                              as total_cost,
  -- Cost per connected call (duration > 0)
  case
    when count(distinct calls.id) filter (where calls.duration_seconds > 0) > 0
    then coalesce(sum(u.total_cost), 0) /
         count(distinct calls.id) filter (where calls.duration_seconds > 0)
    else null
  end                                                         as cost_per_connected_call,
  -- Cost per qualified lead
  case
    when count(distinct calls.id) filter (where calls.outcome = 'qualified') > 0
    then coalesce(sum(u.total_cost), 0) /
         count(distinct calls.id) filter (where calls.outcome = 'qualified')
    else null
  end                                                         as cost_per_qualified_lead,
  -- Cost per site visit booked
  case
    when count(distinct calls.id) filter (where calls.outcome = 'site_visit_booked') > 0
    then coalesce(sum(u.total_cost), 0) /
         count(distinct calls.id) filter (where calls.outcome = 'site_visit_booked')
    else null
  end                                                         as cost_per_site_visit,
  max(u.currency)                                             as currency
from   public.campaigns c
left join public.calls calls    on calls.campaign_id = c.id
left join public.ai_usage_events u on u.call_id = calls.id
where  c.deleted_at is null
  and  c.tenant_id = public.get_current_tenant_id()
group  by c.tenant_id, c.id, c.name;

comment on view public.v_campaign_cost_summary is
  'Campaign-level cost and funnel efficiency. '
  'Shows cost per call, cost per qualified lead, cost per site visit booked. '
  'Tenant-filtered.';
