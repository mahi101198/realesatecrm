-- =============================================================================
-- MIGRATION 008: Communication Tables
-- whatsapp_templates → whatsapp_messages → communication_logs
-- =============================================================================

-- ---------------------------------------------------------------------------
-- WHATSAPP_TEMPLATES
-- ---------------------------------------------------------------------------

create table public.whatsapp_templates (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  name                  text not null,
  display_name          text,
  language              text not null default 'hi',
  category              text not null,                     -- 'MARKETING' | 'UTILITY' | 'AUTHENTICATION'
  provider              text not null default 'meta',
  provider_template_id  text,                              -- Meta's template ID after approval
  header_type           text,                              -- 'text' | 'image' | 'video' | 'document' | none
  header_content        text,
  body_text             text not null,
  footer_text           text,
  buttons               jsonb not null default '[]',       -- CTA/quick reply buttons
  variables             jsonb not null default '[]',       -- Variable placeholder descriptions
  status                public.whatsapp_template_status not null default 'pending',
  rejection_reason      text,
  created_by            uuid references public.users (id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_whatsapp_templates_tenant_name_lang unique (tenant_id, name, language)
);

create index idx_whatsapp_templates_tenant on public.whatsapp_templates (tenant_id);

create trigger trg_whatsapp_templates_updated_at
  before update on public.whatsapp_templates
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- WHATSAPP_MESSAGES
-- ---------------------------------------------------------------------------

create table public.whatsapp_messages (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  lead_id               uuid references public.leads (id) on delete set null,
  template_id           uuid references public.whatsapp_templates (id) on delete set null,
  related_call_id       uuid references public.calls (id) on delete set null,
  -- Message details
  direction             public.message_direction not null,
  provider              text not null default 'meta',
  provider_message_id   text,                              -- Provider's message ID
  wa_id                 text,                              -- WhatsApp phone ID
  phone_to              text,
  phone_from            text,
  -- Content
  message_type          text not null default 'template',  -- 'template' | 'text' | 'image' | 'document' | 'audio'
  content               jsonb not null default '{}',       -- Full message content/payload
  template_variables    jsonb not null default '{}',       -- Variable values used in template
  -- Delivery status
  status                public.whatsapp_message_status not null default 'queued',
  delivered_at          timestamptz,
  read_at               timestamptz,
  failed_at             timestamptz,
  failure_code          text,
  failure_reason        text,
  -- Metadata
  sent_at               timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index idx_wa_messages_customer   on public.whatsapp_messages (tenant_id, customer_id, created_at desc);
create index idx_wa_messages_lead       on public.whatsapp_messages (lead_id, created_at desc) where lead_id is not null;
create index idx_wa_messages_provider_id on public.whatsapp_messages (provider_message_id) where provider_message_id is not null;
create index idx_wa_messages_status     on public.whatsapp_messages (tenant_id, status);

create trigger trg_whatsapp_messages_updated_at
  before update on public.whatsapp_messages
  for each row execute function public.set_updated_at();

comment on table public.whatsapp_messages is
  'WhatsApp inbound and outbound messages. Full content stored in JSONB.';

-- ---------------------------------------------------------------------------
-- COMMUNICATION_LOGS
-- ---------------------------------------------------------------------------
-- Lightweight unified timeline of all outbound communication events.
-- Does NOT duplicate message body; references channel-specific tables.
-- ---------------------------------------------------------------------------

create table public.communication_logs (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  lead_id               uuid references public.leads (id) on delete set null,
  -- Channel reference (only one should be non-null)
  call_id               uuid references public.calls (id) on delete set null,
  whatsapp_message_id   uuid references public.whatsapp_messages (id) on delete set null,
  -- Channel metadata
  channel               public.communication_channel not null,
  direction             public.message_direction not null,
  status                text not null,                     -- Channel-specific status string
  summary               text,                              -- One-line description for timeline
  initiated_by          text not null default 'system',    -- 'ai_agent' | 'user' | 'system'
  initiated_by_id       uuid,
  created_at            timestamptz not null default now()
);

create index idx_comm_logs_customer   on public.communication_logs (tenant_id, customer_id, created_at desc);
create index idx_comm_logs_lead       on public.communication_logs (lead_id, created_at desc) where lead_id is not null;
create index idx_comm_logs_channel    on public.communication_logs (tenant_id, channel, created_at desc);

comment on table public.communication_logs is
  'Unified lightweight communication timeline. References channel-specific tables '
  'instead of duplicating content. Used for CRM timeline display.';
