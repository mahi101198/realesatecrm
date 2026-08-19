-- =============================================================================
-- MIGRATION 006: Voice / AI Call Tables
-- agent_configs → calls → call_participants → call_messages → call_events
-- agent_sessions → conversation_summaries → lead_score_events
-- =============================================================================

-- ---------------------------------------------------------------------------
-- AGENT_CONFIGS
-- ---------------------------------------------------------------------------
-- Configurable AI voice agent persona and settings.
-- API credentials are NEVER stored here.
-- ---------------------------------------------------------------------------

create table public.agent_configs (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  name                  text not null,
  description           text,
  version               integer not null default 1,
  is_active             boolean not null default false,
  -- Language
  language              text not null default 'hi',        -- BCP-47 e.g. "hi", "en-IN"
  -- STT
  stt_provider          text not null default 'deepgram',
  stt_model             text not null default 'nova-3',
  stt_language          text not null default 'hi',
  stt_settings          jsonb not null default '{}',
  -- LLM
  llm_provider          text not null default 'google',
  llm_model             text not null default 'gemma-4-31b-it',
  llm_temperature       numeric(4, 3) not null default 0.7,
  llm_max_tokens        integer not null default 512,
  llm_settings          jsonb not null default '{}',
  -- TTS
  tts_provider          text not null default 'cartesia',
  tts_model             text not null default 'sonic-3',
  tts_voice_id          text,
  tts_settings          jsonb not null default '{}',
  -- Prompt
  system_prompt         text,
  -- Personality & rules (JSONB for flexibility)
  personality           jsonb not null default '{}',       -- tone, style, traits
  sales_rules           jsonb not null default '{}',       -- when to qualify, how to handle objections
  transfer_rules        jsonb not null default '{}',       -- conditions for human transfer
  conversation_rules    jsonb not null default '{}',       -- flow rules
  calling_rules         jsonb not null default '{}',       -- retry, time rules
  -- Metadata
  created_by            uuid references public.users (id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  deleted_at            timestamptz,

  constraint uq_agent_configs_tenant_name_version unique (tenant_id, name, version)
);

create index idx_agent_configs_tenant_id on public.agent_configs (tenant_id);
create index idx_agent_configs_active    on public.agent_configs (tenant_id, is_active) where is_active = true;

create trigger trg_agent_configs_updated_at
  before update on public.agent_configs
  for each row execute function public.set_updated_at();

comment on table public.agent_configs is
  'AI voice agent configuration. Stores model selection, prompts, and rules. '
  'API credentials must NEVER be stored in this table.';

-- Add FK from campaigns to agent_configs (circular dependency resolution)
alter table public.campaigns
  add constraint fk_campaigns_agent_config
  foreign key (agent_config_id) references public.agent_configs (id) on delete set null;

-- ---------------------------------------------------------------------------
-- CALLS
-- ---------------------------------------------------------------------------

create table public.calls (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid references public.leads (id) on delete set null,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  campaign_id           uuid references public.campaigns (id) on delete set null,
  campaign_lead_id      uuid references public.campaign_leads (id) on delete set null,
  agent_config_id       uuid references public.agent_configs (id) on delete set null,
  assigned_sales_agent_id uuid references public.sales_agents (id) on delete set null,
  -- Telephony
  direction             public.call_direction not null default 'outbound',
  provider              text not null default 'supaphone',
  provider_call_id      text,                              -- Provider's unique call ID
  provider_session_id   text,                              -- e.g. LiveKit room/session ID
  phone_from            text,                              -- Caller number
  phone_to              text not null,                     -- Destination number
  -- Status & outcome
  status                public.call_status not null default 'initiated',
  outcome               public.call_outcome,
  -- Timing
  initiated_at          timestamptz not null default now(),
  answered_at           timestamptz,
  ended_at              timestamptz,
  duration_seconds      integer,                           -- Total call wall-clock time
  talk_time_seconds     integer,                           -- Customer+agent speaking time
  -- AI performance metrics
  agent_speak_time_seconds   integer,
  customer_speak_time_seconds integer,
  interruption_count    integer not null default 0,
  avg_stt_latency_ms    integer,
  avg_llm_latency_ms    integer,
  avg_tts_latency_ms    integer,
  avg_response_latency_ms integer,
  -- Usage / cost
  stt_duration_seconds  integer,
  tts_characters        integer,
  llm_input_tokens      integer,
  llm_output_tokens     integer,
  telephony_seconds     integer,
  -- Recording
  recording_path        text,                              -- Supabase Storage path
  recording_url         text,
  -- Summary
  call_summary          text,                              -- Human-readable AI summary
  -- Metadata
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint chk_calls_duration      check (duration_seconds is null or duration_seconds >= 0),
  constraint chk_calls_talk_time     check (talk_time_seconds is null or talk_time_seconds >= 0),
  constraint chk_calls_interruptions check (interruption_count >= 0)
);

create index idx_calls_tenant_lead      on public.calls (tenant_id, lead_id, created_at desc);
create index idx_calls_tenant_customer  on public.calls (tenant_id, customer_id, created_at desc);
create index idx_calls_tenant_campaign  on public.calls (tenant_id, campaign_id, created_at desc);
create index idx_calls_provider_call_id on public.calls (provider_call_id) where provider_call_id is not null;
create index idx_calls_status           on public.calls (tenant_id, status);
create index idx_calls_created_at       on public.calls (tenant_id, created_at desc);

create trigger trg_calls_updated_at
  before update on public.calls
  for each row execute function public.set_updated_at();

comment on table public.calls is
  'Central call record for every AI and human call. Links customer, lead, campaign.';

-- ---------------------------------------------------------------------------
-- Resolve circular FK: sales_agents.current_call_id → calls
-- ---------------------------------------------------------------------------

alter table public.sales_agents
  add constraint fk_sales_agents_current_call
  foreign key (current_call_id) references public.calls (id) on delete set null;

-- ---------------------------------------------------------------------------
-- Resolve circular FKs: property_status_history references calls and leads
-- ---------------------------------------------------------------------------

alter table public.property_status_history
  add constraint fk_prop_status_hist_lead
  foreign key (related_lead_id) references public.leads (id) on delete set null;

alter table public.property_status_history
  add constraint fk_prop_status_hist_call
  foreign key (related_call_id) references public.calls (id) on delete set null;

-- ---------------------------------------------------------------------------
-- CALL_PARTICIPANTS
-- ---------------------------------------------------------------------------

create table public.call_participants (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  call_id               uuid not null references public.calls (id) on delete cascade,
  participant_type      public.call_participant_type not null,
  participant_id        uuid,                              -- FK to customer/user/sales_agent (polymorphic)
  phone                 text,
  display_name          text,
  joined_at             timestamptz,
  left_at               timestamptz,
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now()
);

create index idx_call_participants_call on public.call_participants (call_id);

comment on table public.call_participants is
  'Participants (customer, AI, human agent) in a call. Polymorphic participant_id.';

-- ---------------------------------------------------------------------------
-- CALL_MESSAGES
-- ---------------------------------------------------------------------------
-- Individual utterances within a call. Append-only. High volume.
-- ---------------------------------------------------------------------------

create table public.call_messages (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  call_id               uuid not null references public.calls (id) on delete cascade,
  sequence_number       integer not null,
  speaker               public.message_speaker not null,
  message_type          public.message_type not null default 'speech',
  message_text          text,
  intent                text,                              -- Detected intent e.g. "ask_price"
  emotion               text,                              -- Detected emotion
  confidence            numeric(5, 4),                     -- STT confidence 0.0–1.0
  language              text,                              -- Detected language BCP-47
  utterance_start_ms    bigint,                            -- Ms from call start
  utterance_end_ms      bigint,
  stt_latency_ms        integer,
  tool_name             text,                              -- If message_type = tool_call
  tool_args             jsonb,                             -- Tool call arguments
  tool_result           jsonb,                             -- Tool call result
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now(),

  constraint uq_call_messages_sequence unique (call_id, sequence_number),
  constraint chk_call_messages_confidence check (confidence is null or (confidence >= 0 and confidence <= 1))
);

create index idx_call_messages_call_seq  on public.call_messages (call_id, sequence_number);
create index idx_call_messages_call_time on public.call_messages (call_id, created_at);

comment on table public.call_messages is
  'Individual utterances and tool calls during a call. Append-only. High volume.';

-- ---------------------------------------------------------------------------
-- CALL_EVENTS
-- ---------------------------------------------------------------------------
-- Technical events during a call (separate from spoken messages).
-- Append-only. Critical for debugging voice-agent behavior.
-- ---------------------------------------------------------------------------

create table public.call_events (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  call_id               uuid not null references public.calls (id) on delete cascade,
  event_type            public.call_event_type not null,
  event_time            timestamptz not null default now(),
  latency_ms            integer,                           -- Relevant latency for this event
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now()
);

create index idx_call_events_call      on public.call_events (call_id, event_time);
create index idx_call_events_type      on public.call_events (call_id, event_type);
create index idx_call_events_tenant    on public.call_events (tenant_id, event_time desc);

comment on table public.call_events is
  'Technical lifecycle events for a call. Append-only. Used for latency analysis.';

-- ---------------------------------------------------------------------------
-- AGENT_SESSIONS
-- ---------------------------------------------------------------------------
-- AI agent working state for a call. Durable state that survives Redis flushes.
-- ---------------------------------------------------------------------------

create table public.agent_sessions (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  call_id               uuid not null references public.calls (id) on delete cascade,
  lead_id               uuid references public.leads (id) on delete set null,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  agent_config_id       uuid references public.agent_configs (id) on delete set null,
  -- Session state
  status                public.agent_session_status not null default 'active',
  current_state         text,                              -- FSM state name
  current_intent        text,
  detected_language     text,
  customer_emotion      text,
  -- Extracted context
  conversation_summary  text,                              -- Rolling summary
  extracted_requirements jsonb not null default '{}',     -- Budget, city, etc. extracted by AI
  -- Timing
  started_at            timestamptz not null default now(),
  ended_at              timestamptz,
  -- Metadata
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_agent_sessions_call unique (call_id)
);

create index idx_agent_sessions_call     on public.agent_sessions (call_id);
create index idx_agent_sessions_lead     on public.agent_sessions (lead_id) where lead_id is not null;
create index idx_agent_sessions_customer on public.agent_sessions (customer_id);

create trigger trg_agent_sessions_updated_at
  before update on public.agent_sessions
  for each row execute function public.set_updated_at();

comment on table public.agent_sessions is
  'AI agent durable session state per call. Real-time state lives in Redis.';

-- ---------------------------------------------------------------------------
-- CONVERSATION_SUMMARIES
-- ---------------------------------------------------------------------------
-- Post-call AI analysis and conversation intelligence.
-- ---------------------------------------------------------------------------

create table public.conversation_summaries (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  call_id               uuid not null references public.calls (id) on delete cascade,
  lead_id               uuid references public.leads (id) on delete set null,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  -- Structured summary fields
  summary_text          text,                              -- Full narrative summary
  customer_intent       text,
  customer_emotion      text,
  -- Extracted information
  extracted_budget_min  numeric(15, 2),
  extracted_budget_max  numeric(15, 2),
  extracted_city        text,
  extracted_locality    text,
  extracted_property_type text,
  extracted_timeline    text,
  -- Scoring signals
  interest_level        public.interest_level,
  buying_probability    integer,                           -- 0–100
  site_visit_probability integer,                          -- 0–100
  suggested_lead_score  integer,
  -- Objections and actions
  objections_raised     jsonb not null default '[]',       -- Array of objection strings
  next_best_action      text,
  human_transfer_required boolean not null default false,
  -- Full flexible intelligence
  intelligence          jsonb not null default '{}',       -- All other extracted fields
  -- Metadata
  generated_by_model    text,
  generated_at          timestamptz not null default now(),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_conversation_summaries_call unique (call_id),
  constraint chk_conv_summary_buying_prob
    check (buying_probability is null or (buying_probability between 0 and 100)),
  constraint chk_conv_summary_site_visit_prob
    check (site_visit_probability is null or (site_visit_probability between 0 and 100)),
  constraint chk_conv_summary_lead_score
    check (suggested_lead_score is null or (suggested_lead_score between 0 and 100))
);

create index idx_conv_summaries_call     on public.conversation_summaries (call_id);
create index idx_conv_summaries_lead     on public.conversation_summaries (lead_id) where lead_id is not null;
create index idx_conv_summaries_customer on public.conversation_summaries (customer_id);

create trigger trg_conversation_summaries_updated_at
  before update on public.conversation_summaries
  for each row execute function public.set_updated_at();

comment on table public.conversation_summaries is
  'Post-call conversation intelligence extracted by AI. One per call.';

-- ---------------------------------------------------------------------------
-- LEAD_SCORE_EVENTS
-- ---------------------------------------------------------------------------
-- Append-only history of lead score changes with reason.
-- ---------------------------------------------------------------------------

create table public.lead_score_events (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  call_id               uuid references public.calls (id) on delete set null,
  previous_score        integer not null default 0,
  new_score             integer not null,
  delta                 integer generated always as (new_score - previous_score) stored,
  reason                text,
  scoring_model         text,                              -- e.g. "ai_v2", "manual"
  scored_by             text not null default 'ai',        -- 'ai' | 'user' | 'system'
  scored_by_user_id     uuid references public.users (id) on delete set null,
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now(),

  constraint chk_lead_score_events_prev  check (previous_score between 0 and 100),
  constraint chk_lead_score_events_new   check (new_score between 0 and 100)
);

create index idx_lead_score_events_lead    on public.lead_score_events (lead_id, created_at desc);
create index idx_lead_score_events_tenant  on public.lead_score_events (tenant_id, created_at desc);
create index idx_lead_score_events_call    on public.lead_score_events (call_id) where call_id is not null;

comment on table public.lead_score_events is
  'Append-only history of lead score changes. Used for ML training and audit.';
