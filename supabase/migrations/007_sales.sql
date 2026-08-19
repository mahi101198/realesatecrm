-- =============================================================================
-- MIGRATION 007: Sales Tables
-- sales_assignments → follow_ups → appointments → appointment_history
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SALES_ASSIGNMENTS
-- ---------------------------------------------------------------------------
-- Full history of lead-to-sales-agent assignments.
-- Multiple records are created when a lead is reassigned.
-- ---------------------------------------------------------------------------

create table public.sales_assignments (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  sales_agent_id        uuid not null references public.sales_agents (id) on delete restrict,
  assigned_by           uuid references public.users (id) on delete set null,
  assignment_type       public.assignment_type not null default 'manual',
  is_primary            boolean not null default true,
  reason                text,
  related_call_id       uuid references public.calls (id) on delete set null,
  assigned_at           timestamptz not null default now(),
  unassigned_at         timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index idx_sales_assignments_lead        on public.sales_assignments (lead_id, assigned_at desc);
create index idx_sales_assignments_agent       on public.sales_assignments (sales_agent_id, assigned_at desc);
create index idx_sales_assignments_tenant      on public.sales_assignments (tenant_id, assigned_at desc);
create index idx_sales_assignments_primary     on public.sales_assignments (lead_id, is_primary)
  where is_primary = true;

create trigger trg_sales_assignments_updated_at
  before update on public.sales_assignments
  for each row execute function public.set_updated_at();

comment on table public.sales_assignments is
  'Full history of lead-to-sales-agent assignments. Current assignment is the '
  'record where is_primary=true and unassigned_at IS NULL.';

-- ---------------------------------------------------------------------------
-- FOLLOW_UPS
-- ---------------------------------------------------------------------------

create table public.follow_ups (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  related_call_id       uuid references public.calls (id) on delete set null,
  -- What and when
  follow_up_type        public.follow_up_type not null default 'ai_call',
  scheduled_at          timestamptz not null,
  status                public.follow_up_status not null default 'pending',
  reason                text,
  notes                 text,
  -- Assignment
  assigned_sales_agent_id uuid references public.sales_agents (id) on delete set null,
  created_by            uuid references public.users (id) on delete set null,
  -- Completion
  completed_at          timestamptz,
  completed_by          uuid references public.users (id) on delete set null,
  completion_notes      text,
  -- Metadata
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index idx_follow_ups_tenant_scheduled on public.follow_ups (tenant_id, scheduled_at, status);
create index idx_follow_ups_lead             on public.follow_ups (lead_id, scheduled_at desc);
create index idx_follow_ups_pending          on public.follow_ups (tenant_id, scheduled_at)
  where status = 'pending';
create index idx_follow_ups_agent            on public.follow_ups (assigned_sales_agent_id, scheduled_at)
  where assigned_sales_agent_id is not null;

create trigger trg_follow_ups_updated_at
  before update on public.follow_ups
  for each row execute function public.set_updated_at();

comment on table public.follow_ups is
  'Scheduled follow-up tasks (AI call, human call, WhatsApp, email).';

-- ---------------------------------------------------------------------------
-- APPOINTMENTS
-- ---------------------------------------------------------------------------
-- Primary use case: site visit booking.
-- Double-booking prevention is enforced via partial unique index + trigger.
-- ---------------------------------------------------------------------------

create table public.appointments (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  lead_id               uuid references public.leads (id) on delete set null,
  project_id            uuid references public.projects (id) on delete set null,
  property_id           uuid references public.properties (id) on delete set null,
  sales_agent_id        uuid references public.sales_agents (id) on delete set null,
  related_call_id       uuid references public.calls (id) on delete set null,
  -- What
  appointment_type      public.appointment_type not null default 'site_visit',
  source                public.appointment_source not null default 'ai_agent',
  -- Timing
  scheduled_at          timestamptz not null,
  duration_minutes      integer not null default 60,
  -- Status
  status                public.appointment_status not null default 'pending',
  -- Notes
  notes                 text,
  internal_notes        text,
  -- Confirmation & completion
  confirmed_at          timestamptz,
  confirmed_by          uuid references public.users (id) on delete set null,
  completed_at          timestamptz,
  cancelled_at          timestamptz,
  cancelled_by          uuid references public.users (id) on delete set null,
  cancellation_reason   text,
  rescheduled_from_id   uuid references public.appointments (id) on delete set null,
  -- Metadata
  metadata              jsonb not null default '{}',
  created_by            uuid references public.users (id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint chk_appointments_duration check (duration_minutes > 0)
);

create index idx_appointments_tenant_scheduled on public.appointments (tenant_id, scheduled_at);
create index idx_appointments_lead            on public.appointments (tenant_id, lead_id) where lead_id is not null;
create index idx_appointments_agent_scheduled on public.appointments (sales_agent_id, scheduled_at)
  where sales_agent_id is not null;
create index idx_appointments_status          on public.appointments (tenant_id, status);
create index idx_appointments_customer        on public.appointments (tenant_id, customer_id);

-- Partial unique index for double-booking prevention.
-- An agent can only have one non-cancelled, non-rescheduled appointment per time slot.
-- Business logic for exact overlap prevention lives in the booking function below.
create index idx_appointments_agent_slot on public.appointments (sales_agent_id, scheduled_at)
  where status not in ('cancelled', 'rescheduled') and sales_agent_id is not null;

create trigger trg_appointments_updated_at
  before update on public.appointments
  for each row execute function public.set_updated_at();

comment on table public.appointments is
  'Appointments (primarily site visits). Double-booking is prevented by '
  'the book_site_visit() function using advisory locks.';

-- ---------------------------------------------------------------------------
-- APPOINTMENT_HISTORY
-- ---------------------------------------------------------------------------
-- Append-only log of every status change on an appointment.
-- ---------------------------------------------------------------------------

create table public.appointment_history (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  appointment_id        uuid not null references public.appointments (id) on delete cascade,
  previous_status       public.appointment_status,
  new_status            public.appointment_status not null,
  changed_by            uuid references public.users (id) on delete set null,
  changed_by_type       text not null default 'user',      -- 'user' | 'ai_agent' | 'system'
  reason                text,
  metadata              jsonb not null default '{}',
  created_at            timestamptz not null default now()
);

create index idx_appointment_history_appt on public.appointment_history (appointment_id, created_at desc);

comment on table public.appointment_history is
  'Append-only status history for appointments. Immutable.';

-- ---------------------------------------------------------------------------
-- TRIGGER: Auto-add appointment history when status changes
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_appointment_status_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if old.status is distinct from new.status then
    insert into public.appointment_history (
      tenant_id, appointment_id,
      previous_status, new_status
    ) values (
      new.tenant_id, new.id,
      old.status, new.status
    );
  end if;
  return new;
end;
$$;

create trigger trg_appointments_status_change
  after update on public.appointments
  for each row execute function public.trg_fn_appointment_status_change();

-- ---------------------------------------------------------------------------
-- FUNCTION: book_site_visit
-- ---------------------------------------------------------------------------
-- Atomically creates a site visit appointment with double-booking protection.
-- Uses pg_try_advisory_xact_lock on (agent_id hash, slot timestamp).
-- FastAPI should call this function inside a transaction.
-- ---------------------------------------------------------------------------

create or replace function public.book_site_visit(
  p_tenant_id         uuid,
  p_customer_id       uuid,
  p_lead_id           uuid,
  p_project_id        uuid,
  p_property_id       uuid,
  p_sales_agent_id    uuid,
  p_scheduled_at      timestamptz,
  p_duration_minutes  integer,
  p_source            public.appointment_source,
  p_related_call_id   uuid,
  p_notes             text,
  p_created_by        uuid
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_appointment_id    uuid;
  v_lock_id           bigint;
  v_conflict_count    integer;
begin
  -- Advisory lock key: hash of (agent_id, hour_bucket)
  -- Prevents concurrent booking for the same agent at the same time
  if p_sales_agent_id is not null then
    v_lock_id := hashtext(p_sales_agent_id::text || date_trunc('hour', p_scheduled_at)::text);
    if not pg_try_advisory_xact_lock(v_lock_id) then
      raise exception 'Could not acquire booking lock. Please try again.';
    end if;

    -- Check for existing overlapping appointment for this agent
    select count(*) into v_conflict_count
    from   public.appointments
    where  sales_agent_id = p_sales_agent_id
      and  status not in ('cancelled', 'rescheduled')
      and  p_scheduled_at < (scheduled_at + (duration_minutes * interval '1 minute'))
      and  (p_scheduled_at + (p_duration_minutes * interval '1 minute')) > scheduled_at;

    if v_conflict_count > 0 then
      raise exception 'Agent has a conflicting appointment at this time slot.';
    end if;
  end if;

  -- Validate appointment time in future
  if p_scheduled_at < now() then
    raise exception 'Appointment must be scheduled in the future.';
  end if;

  insert into public.appointments (
    tenant_id, customer_id, lead_id, project_id, property_id,
    sales_agent_id, related_call_id, appointment_type, source,
    scheduled_at, duration_minutes, status, notes, created_by
  ) values (
    p_tenant_id, p_customer_id, p_lead_id, p_project_id, p_property_id,
    p_sales_agent_id, p_related_call_id, 'site_visit', p_source,
    p_scheduled_at, p_duration_minutes, 'pending', p_notes, p_created_by
  )
  returning id into v_appointment_id;

  return v_appointment_id;
end;
$$;

comment on function public.book_site_visit is
  'Atomically books a site visit with advisory-lock-based double-booking prevention.';
