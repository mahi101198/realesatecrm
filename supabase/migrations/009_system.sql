-- =============================================================================
-- MIGRATION 009: System Tables
-- activities → notifications → audit_logs → integrations → webhook_events
-- =============================================================================

-- ---------------------------------------------------------------------------
-- ACTIVITIES
-- ---------------------------------------------------------------------------
-- CRM activity timeline. Append-only. One record per business event.
-- Shown as the main timeline in a lead/customer detail view.
-- ---------------------------------------------------------------------------

create table public.activities (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  customer_id           uuid references public.customers (id) on delete set null,
  lead_id               uuid references public.leads (id) on delete set null,
  call_id               uuid references public.calls (id) on delete set null,
  appointment_id        uuid references public.appointments (id) on delete set null,
  -- Activity type and actor
  activity_type         text not null,                     -- e.g. "lead_created", "call_completed"
  actor_type            text not null default 'system',    -- 'user' | 'ai_agent' | 'system'
  actor_id              uuid,                              -- user_id or agent config id
  -- Display
  title                 text,
  description           text,
  -- Flexible payload for extra context
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now()
);

create index idx_activities_lead      on public.activities (tenant_id, lead_id, created_at desc)
  where lead_id is not null;
create index idx_activities_customer  on public.activities (tenant_id, customer_id, created_at desc)
  where customer_id is not null;
create index idx_activities_tenant    on public.activities (tenant_id, created_at desc);
create index idx_activities_type      on public.activities (tenant_id, activity_type, created_at desc);

comment on table public.activities is
  'Append-only CRM activity timeline. Used for lead and customer history views.';

-- ---------------------------------------------------------------------------
-- NOTIFICATIONS
-- ---------------------------------------------------------------------------

create table public.notifications (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  user_id               uuid not null references public.users (id) on delete cascade,
  type                  public.notification_type not null,
  title                 text not null,
  message               text not null,
  -- Reference to subject entity
  entity_type           text,                              -- 'lead' | 'call' | 'appointment' | ...
  entity_id             uuid,
  -- Deep link
  action_url            text,
  -- Status
  is_read               boolean not null default false,
  read_at               timestamptz,
  -- Metadata
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now()
);

create index idx_notifications_user      on public.notifications (user_id, is_read, created_at desc);
create index idx_notifications_unread    on public.notifications (user_id, created_at desc)
  where is_read = false;
create index idx_notifications_tenant    on public.notifications (tenant_id, created_at desc);

comment on table public.notifications is
  'In-app notifications for CRM users. Hot leads, transfers, reminders etc.';

-- ---------------------------------------------------------------------------
-- AUDIT_LOGS
-- ---------------------------------------------------------------------------
-- Append-only. Records important data changes for security and compliance.
-- ---------------------------------------------------------------------------

create table public.audit_logs (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid references public.tenants (id) on delete set null,
  user_id               uuid references public.users (id) on delete set null,
  action                text not null,                     -- e.g. "lead.updated", "user.role_changed"
  entity_type           text not null,
  entity_id             uuid,
  old_data              jsonb,                             -- Previous state (redacted if sensitive)
  new_data              jsonb,                             -- New state
  changed_fields        text[],                            -- List of changed field names
  ip_address            inet,
  user_agent            text,
  request_id            text,                              -- Correlation ID from API
  created_at            timestamptz not null default now()
);

create index idx_audit_logs_tenant      on public.audit_logs (tenant_id, created_at desc);
create index idx_audit_logs_user        on public.audit_logs (user_id, created_at desc);
create index idx_audit_logs_entity      on public.audit_logs (entity_type, entity_id, created_at desc);
create index idx_audit_logs_action      on public.audit_logs (action, created_at desc);

comment on table public.audit_logs is
  'Append-only security audit log. Immutable. Records changes to important entities.';

-- ---------------------------------------------------------------------------
-- INTEGRATIONS
-- ---------------------------------------------------------------------------
-- Non-sensitive integration configuration (credentials NOT stored here).
-- ---------------------------------------------------------------------------

create table public.integrations (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete cascade,
  provider              text not null,                     -- 'supaphone' | 'deepgram' | 'cartesia' | 'meta' etc.
  integration_type      text not null,                     -- 'telephony' | 'stt' | 'tts' | 'llm' | 'whatsapp' | 'calendar'
  name                  text not null,
  status                public.integration_status not null default 'pending_setup',
  -- Non-sensitive config (keys stored in Vault / secrets manager)
  config                jsonb not null default '{}',
  -- References to secrets
  secret_key_ref        text,                              -- Reference name in secrets manager (NOT the key itself)
  last_synced_at        timestamptz,
  last_error            text,
  created_by            uuid references public.users (id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_integrations_tenant_provider_type unique (tenant_id, provider, integration_type)
);

create index idx_integrations_tenant on public.integrations (tenant_id);

create trigger trg_integrations_updated_at
  before update on public.integrations
  for each row execute function public.set_updated_at();

comment on table public.integrations is
  'Integration metadata. Secrets are stored in Vault/secrets manager, '
  'referenced by secret_key_ref only.';

-- ---------------------------------------------------------------------------
-- WEBHOOK_EVENTS
-- ---------------------------------------------------------------------------
-- Stores raw inbound webhook payloads for idempotent processing.
-- ---------------------------------------------------------------------------

create table public.webhook_events (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid references public.tenants (id) on delete set null,
  provider              text not null,                     -- 'supaphone' | 'meta' | 'deepgram' etc.
  event_type            text not null,                     -- Provider-specific event type string
  external_event_id     text,                              -- Provider's event ID for dedup
  payload               jsonb not null,                    -- Full raw webhook payload
  processing_status     public.webhook_status not null default 'received',
  processed_at          timestamptz,
  error_message         text,
  retry_count           integer not null default 0,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  -- Idempotency: same external event cannot be processed twice
  constraint uq_webhook_events_provider_external unique (provider, external_event_id)
    deferrable initially deferred
);

create index idx_webhook_events_status   on public.webhook_events (processing_status, created_at desc);
create index idx_webhook_events_provider on public.webhook_events (provider, event_type, created_at desc);
create index idx_webhook_events_tenant   on public.webhook_events (tenant_id, created_at desc)
  where tenant_id is not null;

create trigger trg_webhook_events_updated_at
  before update on public.webhook_events
  for each row execute function public.set_updated_at();

comment on table public.webhook_events is
  'Raw inbound webhook storage with idempotency protection. '
  'external_event_id uniquely identifies an event per provider.';
