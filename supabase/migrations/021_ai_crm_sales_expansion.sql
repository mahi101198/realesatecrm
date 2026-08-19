-- =============================================================================
-- MIGRATION 021: AI Real-Estate Sales CRM Expansion
-- call_jobs -> call_attempts -> lead_follow_ups -> lead_observations
-- lead_requirement_history -> agent_call_contexts -> lead_scores
-- lead_contact_preferences -> sales_handoffs
-- =============================================================================

-- ---------------------------------------------------------------------------
-- ENUMS FOR CALL JOBS & CALL ATTEMPTS
-- ---------------------------------------------------------------------------

do $$ begin
  if not exists (select 1 from pg_type where typname = 'call_job_type') then
    create type public.call_job_type as enum (
      'initial_lead_call',
      'follow_up_call',
      'callback',
      'human_callback'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'call_job_status') then
    create type public.call_job_status as enum (
      'queued',
      'scheduled',
      'preparing',
      'ready',
      'calling',
      'completed',
      'retry_pending',
      'failed',
      'cancelled'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'call_attempt_outcome') then
    create type public.call_attempt_outcome as enum (
      'connected',
      'no_answer',
      'busy',
      'rejected',
      'wrong_number',
      'customer_requested_callback',
      'customer_not_interested',
      'site_visit_scheduled',
      'human_transfer',
      'technical_failure'
    );
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- CALL_JOBS
-- ---------------------------------------------------------------------------

create table if not exists public.call_jobs (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,

  job_type              public.call_job_type not null default 'initial_lead_call',
  priority              integer not null default 5,
  status                public.call_job_status not null default 'queued',

  scheduled_at          timestamptz not null default now(),
  timezone              text not null default 'Asia/Kolkata',

  attempt_count         integer not null default 0,
  max_attempts          integer not null default 3,
  next_attempt_at       timestamptz,

  started_at            timestamptz,
  completed_at          timestamptz,

  call_id               uuid references public.calls (id) on delete set null,
  agent_session_id      text,

  last_error_code       text,
  last_error_message    text,

  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists idx_call_jobs_tenant_status on public.call_jobs (tenant_id, status, scheduled_at);
create index if not exists idx_call_jobs_lead on public.call_jobs (tenant_id, lead_id);
create index if not exists idx_call_jobs_customer on public.call_jobs (tenant_id, customer_id);

create trigger trg_call_jobs_updated_at
  before update on public.call_jobs
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- CALL_ATTEMPTS
-- ---------------------------------------------------------------------------

create table if not exists public.call_attempts (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  call_job_id           uuid references public.call_jobs (id) on delete cascade,

  attempt_number        integer not null default 1,

  status                text not null default 'completed',
  outcome               public.call_attempt_outcome,

  started_at            timestamptz not null default now(),
  answered_at           timestamptz,
  ended_at              timestamptz,
  duration_seconds      integer,

  provider_call_id      text,
  agent_session_id      text,

  termination_reason    text,
  failure_code          text,
  failure_message       text,

  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists idx_call_attempts_job on public.call_attempts (call_job_id);
create index if not exists idx_call_attempts_lead on public.call_attempts (tenant_id, lead_id, created_at desc);

create trigger trg_call_attempts_updated_at
  before update on public.call_attempts
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- LEAD_FOLLOW_UPS
-- ---------------------------------------------------------------------------

create table if not exists public.lead_follow_ups (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,

  follow_up_type        text not null default 'ai_call',
  status                text not null default 'scheduled', -- scheduled, due, queued, in_progress, completed, cancelled, expired

  reason                text,
  scheduled_at          timestamptz not null,
  timezone              text not null default 'Asia/Kolkata',

  preferred_start_time  time,
  preferred_end_time    time,

  source                text not null default 'ai_agent',
  source_call_attempt_id uuid references public.call_attempts (id) on delete set null,

  assigned_user_id      uuid references public.users (id) on delete set null,

  completed_at          timestamptz,
  cancelled_at          timestamptz,
  cancel_reason         text,

  notes                 text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists idx_lead_follow_ups_tenant_scheduled on public.lead_follow_ups (tenant_id, scheduled_at, status);
create index if not exists idx_lead_follow_ups_lead on public.lead_follow_ups (lead_id, scheduled_at desc);

create trigger trg_lead_follow_ups_updated_at
  before update on public.lead_follow_ups
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- LEAD_OBSERVATIONS
-- ---------------------------------------------------------------------------

create table if not exists public.lead_observations (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,

  observation_type      text not null, -- objection, interest_signal, competitor_mentioned, decision_maker, purchase_timeline, financing_requirement, location_preference, property_preference, customer_preference, important_note
  observation_value     text not null,

  confidence            numeric(3, 2) not null default 1.00,

  source                text not null default 'ai_call',
  source_call_attempt_id uuid references public.call_attempts (id) on delete set null,

  created_by_type       text not null default 'ai_agent',
  created_by_user_id    uuid references public.users (id) on delete set null,

  created_at            timestamptz not null default now()
);

create index if not exists idx_lead_observations_lead on public.lead_observations (tenant_id, lead_id, created_at desc);
create index if not exists idx_lead_observations_type on public.lead_observations (tenant_id, lead_id, observation_type);

-- ---------------------------------------------------------------------------
-- EXTEND LEAD_PROPERTY_INTERESTS
-- ---------------------------------------------------------------------------

alter table public.lead_property_interests
  add column if not exists source_call_attempt_id uuid references public.call_attempts (id) on delete set null,
  add column if not exists first_interested_at timestamptz default now(),
  add column if not exists last_interested_at timestamptz default now(),
  add column if not exists status text default 'active';

-- ---------------------------------------------------------------------------
-- LEAD_REQUIREMENT_HISTORY
-- ---------------------------------------------------------------------------

create table if not exists public.lead_requirement_history (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,

  field_name            text not null,
  old_value             text,
  new_value             text,

  source                text not null default 'ai_agent',
  source_call_attempt_id uuid references public.call_attempts (id) on delete set null,

  changed_at            timestamptz not null default now()
);

create index if not exists idx_lead_req_history_lead on public.lead_requirement_history (tenant_id, lead_id, changed_at desc);

-- ---------------------------------------------------------------------------
-- AGENT_CALL_CONTEXTS
-- ---------------------------------------------------------------------------

create table if not exists public.agent_call_contexts (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,

  call_job_id           uuid references public.call_jobs (id) on delete cascade,
  call_attempt_id       uuid references public.call_attempts (id) on delete set null,

  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,

  context_version       integer not null default 1,
  context_payload       jsonb not null default '{}',

  created_at            timestamptz not null default now()
);

create index if not exists idx_agent_call_contexts_job on public.agent_call_contexts (call_job_id);
create index if not exists idx_agent_call_contexts_lead on public.agent_call_contexts (tenant_id, lead_id, created_at desc);

-- ---------------------------------------------------------------------------
-- LEAD_SCORES
-- ---------------------------------------------------------------------------

create table if not exists public.lead_scores (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,

  score                 integer not null,
  temperature           text not null, -- hot, warm, cold
  score_version         integer not null default 1,

  calculated_at         timestamptz not null default now(),
  reason_summary        text
);

create index if not exists idx_lead_scores_lead on public.lead_scores (tenant_id, lead_id, calculated_at desc);

-- ---------------------------------------------------------------------------
-- LEAD_CONTACT_PREFERENCES
-- ---------------------------------------------------------------------------

create table if not exists public.lead_contact_preferences (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,

  preferred_contact_start time,
  preferred_contact_end   time,

  preferred_timezone    text not null default 'Asia/Kolkata',
  preferred_days        text[],

  do_not_call           boolean not null default false,
  preferred_channel     text not null default 'phone',

  updated_from_call_id  uuid references public.calls (id) on delete set null,

  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_lead_contact_preferences_lead unique (lead_id)
);

create index if not exists idx_lead_contact_prefs_customer on public.lead_contact_preferences (tenant_id, customer_id);

create trigger trg_lead_contact_preferences_updated_at
  before update on public.lead_contact_preferences
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- SALES_HANDOFFS
-- ---------------------------------------------------------------------------

create table if not exists public.sales_handoffs (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,

  reason                text,
  priority              integer not null default 5,
  status                text not null default 'requested', -- requested, queued, assigned, accepted, completed, cancelled

  assigned_user_id      uuid references public.users (id) on delete set null,

  requested_at          timestamptz not null default now(),
  accepted_at           timestamptz,
  completed_at          timestamptz,

  source_call_attempt_id uuid references public.call_attempts (id) on delete set null,

  notes                 text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists idx_sales_handoffs_tenant_status on public.sales_handoffs (tenant_id, status, requested_at desc);
create index if not exists idx_sales_handoffs_lead on public.sales_handoffs (lead_id);

create trigger trg_sales_handoffs_updated_at
  before update on public.sales_handoffs
  for each row execute function public.set_updated_at();
