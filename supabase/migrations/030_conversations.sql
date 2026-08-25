-- =============================================================================
-- MIGRATION 030: Conversations (deterministic foundation layer)
--
-- A `conversation` is the durable thread a contact (public.customers) has with
-- this tenant on ONE channel. It is the join point a future orchestrator uses
-- to correlate WhatsApp messages and voice-call utterances that belong to the
-- same ongoing exchange.
--
-- Deliberately minimal in this phase: no summaries, no AI state, no routing.
-- Exactly one OPEN conversation per (tenant_id, contact_id, channel) --
-- enforced by a partial unique index so the race-safe
-- ON CONFLICT DO NOTHING get-or-create in app/conversations/repository.py has
-- a real arbiter to name (the same pattern as
-- uq_customers_tenant_phone_active, migration 014).
--
-- The `conversation_id` columns added to whatsapp_messages / call_messages are
-- NULLABLE and additive: every historical row keeps conversation_id = NULL and
-- every existing INSERT/SELECT in the backend keeps working unchanged.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- ENUMS
-- ---------------------------------------------------------------------------

do $$ begin
  if not exists (select 1 from pg_type where typname = 'conversation_channel') then
    create type public.conversation_channel as enum (
      'whatsapp',
      'voice'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'conversation_status') then
    create type public.conversation_status as enum (
      'open',
      'closed'
    );
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- CONVERSATIONS
-- ---------------------------------------------------------------------------

create table if not exists public.conversations (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  contact_id            uuid not null references public.customers (id) on delete restrict,
  lead_id               uuid references public.leads (id) on delete set null,

  channel               public.conversation_channel not null,
  -- Channel-native thread key (WhatsApp wa_id, provider call/session id, ...).
  -- Informational: correlation is by (tenant, contact, channel), not by this.
  external_thread_id    text,

  status                public.conversation_status not null default 'open',

  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists idx_conversations_tenant_contact
  on public.conversations (tenant_id, contact_id, channel, created_at desc);

create index if not exists idx_conversations_tenant_status
  on public.conversations (tenant_id, status, updated_at desc);

create index if not exists idx_conversations_lead
  on public.conversations (tenant_id, lead_id, created_at desc)
  where lead_id is not null;

create index if not exists idx_conversations_external_thread
  on public.conversations (tenant_id, channel, external_thread_id)
  where external_thread_id is not null;

-- The ON CONFLICT arbiter for get_or_create_conversation. Partial, so a
-- CLOSED conversation never blocks opening a fresh one for the same contact.
-- Predicate written with the explicit cast so it is textually identical to the
-- ON CONFLICT ... WHERE clause in app/conversations/repository.py, leaving no
-- doubt that Postgres can infer this index as the arbiter.
create unique index if not exists uq_conversations_open_contact_channel
  on public.conversations (tenant_id, contact_id, channel)
  where status = 'open'::public.conversation_status;

create trigger trg_conversations_updated_at
  before update on public.conversations
  for each row execute function public.set_updated_at();

comment on table public.conversations is
  'One durable thread per (tenant, contact, channel). At most one OPEN row per '
  'combination -- see uq_conversations_open_contact_channel.';

comment on index public.uq_conversations_open_contact_channel is
  'Arbiter for the race-safe ON CONFLICT DO NOTHING get-or-create in '
  'app/conversations/repository.py. Partial: closed threads are not deduplicated.';

-- ---------------------------------------------------------------------------
-- ROW LEVEL SECURITY
-- Pattern matches migrations 010/018/019/023/024: service_role bypass, then
-- is_super_admin() OR (tenant match AND permission) for authenticated users.
-- The backend always connects as service_role; `authenticated` gets read-only
-- access, mirroring whatsapp_messages / communication_logs (migration 010).
-- ---------------------------------------------------------------------------

alter table public.conversations enable row level security;

create policy "conversations: service_role full access"
  on public.conversations for all
  to service_role using (true) with check (true);

create policy "conversations: tenant read"
  on public.conversations for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('customer.read'))
  );

-- ---------------------------------------------------------------------------
-- ADDITIVE, BACKWARD-COMPATIBLE LINK COLUMNS
-- Nullable on purpose: historical rows predate conversations and stay NULL.
-- ---------------------------------------------------------------------------

alter table public.whatsapp_messages
  add column if not exists conversation_id uuid
  references public.conversations (id) on delete set null;

create index if not exists idx_wa_messages_conversation
  on public.whatsapp_messages (tenant_id, conversation_id, created_at desc)
  where conversation_id is not null;

comment on column public.whatsapp_messages.conversation_id is
  'Nullable link to public.conversations. NULL for rows written before '
  'migration 030; populated going forward by the inbound webhook handler.';

alter table public.call_messages
  add column if not exists conversation_id uuid
  references public.conversations (id) on delete set null;

create index if not exists idx_call_messages_conversation
  on public.call_messages (tenant_id, conversation_id, created_at desc)
  where conversation_id is not null;

comment on column public.call_messages.conversation_id is
  'Nullable link to public.conversations. NULL for rows written before '
  'migration 030; reserved for the voice pipeline in a later phase.';
