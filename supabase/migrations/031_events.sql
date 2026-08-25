-- =============================================================================
-- MIGRATION 031: Domain Event Log (deterministic foundation layer)
--
-- Append-only, DB-only record of the business transitions the platform already
-- performs (contact created, lead created, message received, call
-- requested/scheduled/started/completed/failed, human handoff requested, ...).
--
-- THIS PHASE IS A LOG, NOT A BUS. Nothing subscribes to it; nothing dispatches
-- from it. app/events/publisher.py simply INSERTs a row in the same session as
-- the transition it describes, so the event is committed atomically with the
-- state change it records (or rolled back with it). A later phase may add real
-- dispatch on top of this table without changing any of the call sites.
--
-- event_type is TEXT, not an enum, on purpose: a new event type must not
-- require a migration + deploy lockstep. The authoritative list of values the
-- backend emits lives in app/events/model.py::EventType.
-- =============================================================================

create table if not exists public.events (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,

  event_type            text not null,

  contact_id            uuid references public.customers (id) on delete set null,
  lead_id               uuid references public.leads (id) on delete set null,
  conversation_id       uuid references public.conversations (id) on delete set null,

  payload               jsonb not null default '{}',

  created_at            timestamptz not null default now()
);

-- Primary read path: "what happened for tenant X, of type Y, recently".
create index if not exists idx_events_tenant_type_created
  on public.events (tenant_id, event_type, created_at desc);

create index if not exists idx_events_tenant_created
  on public.events (tenant_id, created_at desc);

create index if not exists idx_events_contact
  on public.events (tenant_id, contact_id, created_at desc)
  where contact_id is not null;

create index if not exists idx_events_lead
  on public.events (tenant_id, lead_id, created_at desc)
  where lead_id is not null;

create index if not exists idx_events_conversation
  on public.events (tenant_id, conversation_id, created_at desc)
  where conversation_id is not null;

comment on table public.events is
  'Append-only domain event log. Written by app/events/publisher.py in the same '
  'transaction as the state change it records. No dispatch/bus in this phase.';

comment on column public.events.event_type is
  'Free-form text; the values this backend emits are enumerated in '
  'app/events/model.py::EventType. Deliberately not a Postgres enum so adding '
  'an event type never requires a migration.';

-- ---------------------------------------------------------------------------
-- ROW LEVEL SECURITY
-- Append-only from `authenticated`'s point of view: SELECT only, no INSERT /
-- UPDATE / DELETE policy at all. The backend writes as service_role, exactly
-- like every other write path in this codebase.
-- Pattern matches migrations 010 (activities/whatsapp_messages) and 024.
-- ---------------------------------------------------------------------------

alter table public.events enable row level security;

create policy "events: service_role full access"
  on public.events for all
  to service_role using (true) with check (true);

create policy "events: tenant read"
  on public.events for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('lead.read'))
  );
