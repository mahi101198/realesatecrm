-- =============================================================================
-- MIGRATION 013: Appointment Concurrency Fix
-- Replaces hour-bucket advisory lock with PostgreSQL EXCLUDE USING gist
-- constraint on a stored tstzrange column. This is the ONLY reliable way
-- to prevent overlapping appointments at the database level.
--
-- ISSUE: The prior book_site_visit() used:
--   hashtext(agent_id || date_trunc('hour', scheduled_at))
-- Two appointments spanning different hours got different lock keys and could
-- both pass the conflict check before either committed.
--
-- FIX: Add appointment_range tstzrange STORED generated column +
--      EXCLUDE USING gist (sales_agent_id WITH =, appointment_range WITH &&)
--      WHERE (status NOT IN ('cancelled','rescheduled'))
-- The exclusion constraint fires atomically at INSERT/UPDATE time.
-- =============================================================================

-- btree_gist is required for combining btree (=) and gist (&&) in one
-- exclusion constraint. Already installed in 001 but re-confirm.
create extension if not exists "btree_gist";

-- ---------------------------------------------------------------------------
-- Step 1: Add generated appointment_range column
-- ---------------------------------------------------------------------------

alter table public.appointments
  add column if not exists appointment_range tstzrange
    generated always as (
      tstzrange(
        scheduled_at,
        scheduled_at + (duration_minutes * interval '1 minute'),
        '[)'
      )
    ) stored;

comment on column public.appointments.appointment_range is
  'Generated half-open range [scheduled_at, scheduled_at + duration). '
  'Used by the exclusion constraint to prevent overlapping appointments.';

-- ---------------------------------------------------------------------------
-- Step 2: Add exclusion constraint
-- Guarantees: no two active (non-cancelled/rescheduled) appointments for
-- the same sales agent can have overlapping time ranges.
-- ---------------------------------------------------------------------------

-- For any existing overlapping rows from old data, we need to fix them first
-- before adding the constraint. This query detects conflicts:
do $$
declare
  v_count integer;
begin
  select count(*) into v_count
  from public.appointments a1
  join public.appointments a2
    on a1.sales_agent_id = a2.sales_agent_id
   and a1.id <> a2.id
   and a1.appointment_range && a2.appointment_range
   and a1.status not in ('cancelled','rescheduled')
   and a2.status not in ('cancelled','rescheduled')
  where a1.sales_agent_id is not null;

  if v_count > 0 then
    raise warning
      'Found % overlapping appointment pairs. Cancel duplicates before '
      'the exclusion constraint can be added.', v_count;
  end if;
end;
$$;

alter table public.appointments
  add constraint excl_appointments_agent_no_overlap
  exclude using gist (
    sales_agent_id  with =,
    appointment_range with &&
  )
  where (status not in ('cancelled', 'rescheduled') and sales_agent_id is not null);

comment on constraint excl_appointments_agent_no_overlap on public.appointments is
  'Database-level guarantee: no agent can have two overlapping active appointments. '
  'Fires atomically at INSERT and UPDATE, making race conditions impossible.';

-- GiST index on the range for performance
create index if not exists idx_appointments_agent_range
  on public.appointments using gist (sales_agent_id, appointment_range)
  where status not in ('cancelled','rescheduled') and sales_agent_id is not null;

-- ---------------------------------------------------------------------------
-- Step 3: Replace book_site_visit() with corrected version
-- Changes:
--  - Validates all tenant references (issue 13)
--  - Uses exclusion constraint as real guard (advisory lock is fast pre-check only)
--  - Returns descriptive error codes
--  - Validates appointment in future BEFORE lock acquisition
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
  v_appointment_id  uuid;
  v_lock_id         bigint;
begin
  -- ----------------------------------------------------------------
  -- Validate: appointment must be in the future
  -- ----------------------------------------------------------------
  if p_scheduled_at < now() then
    raise exception 'Appointment must be scheduled in the future.'
      using errcode = 'P0001';
  end if;

  if p_duration_minutes <= 0 then
    raise exception 'Duration must be positive.'
      using errcode = 'P0001';
  end if;

  -- ----------------------------------------------------------------
  -- Validate: all referenced entities must belong to p_tenant_id
  -- (prevents cross-tenant booking when service_role bypasses RLS)
  -- ----------------------------------------------------------------
  if not exists (
    select 1 from public.customers
    where id = p_customer_id
      and tenant_id = p_tenant_id
      and deleted_at is null
  ) then
    raise exception 'Customer does not belong to tenant or does not exist.'
      using errcode = 'P0002';
  end if;

  if p_lead_id is not null then
    if not exists (
      select 1 from public.leads
      where id = p_lead_id
        and tenant_id = p_tenant_id
        and deleted_at is null
    ) then
      raise exception 'Lead does not belong to tenant or does not exist.'
        using errcode = 'P0002';
    end if;
  end if;

  if p_project_id is not null then
    if not exists (
      select 1 from public.projects
      where id = p_project_id
        and tenant_id = p_tenant_id
        and deleted_at is null
    ) then
      raise exception 'Project does not belong to tenant or does not exist.'
        using errcode = 'P0002';
    end if;
  end if;

  if p_property_id is not null then
    if not exists (
      select 1 from public.properties
      where id = p_property_id
        and tenant_id = p_tenant_id
        and deleted_at is null
    ) then
      raise exception 'Property does not belong to tenant or does not exist.'
        using errcode = 'P0002';
    end if;
  end if;

  if p_sales_agent_id is not null then
    if not exists (
      select 1 from public.sales_agents
      where id = p_sales_agent_id
        and tenant_id = p_tenant_id
        and is_active = true
        and deleted_at is null
    ) then
      raise exception 'Sales agent does not belong to tenant or is inactive.'
        using errcode = 'P0002';
    end if;

    -- Advisory lock on (agent_id, exact_start_time) as a fast pre-check.
    -- This is NOT the sole guard — the exclusion constraint handles correctness.
    -- This prevents unnecessary constraint-violation rollbacks on hot agents.
    v_lock_id := hashtext(p_sales_agent_id::text || p_scheduled_at::text);
    if not pg_try_advisory_xact_lock(v_lock_id) then
      raise exception 'Concurrent booking attempt for this agent. Please retry.'
        using errcode = 'P0003';
    end if;
  end if;

  -- ----------------------------------------------------------------
  -- Insert appointment — exclusion constraint is the real protection
  -- ----------------------------------------------------------------
  begin
    insert into public.appointments (
      tenant_id,      customer_id,    lead_id,        project_id,
      property_id,    sales_agent_id, related_call_id, appointment_type,
      source,         scheduled_at,   duration_minutes, status,
      notes,          created_by
    ) values (
      p_tenant_id,      p_customer_id,    p_lead_id,        p_project_id,
      p_property_id,    p_sales_agent_id, p_related_call_id, 'site_visit',
      p_source,         p_scheduled_at,   p_duration_minutes, 'pending',
      p_notes,          p_created_by
    )
    returning id into v_appointment_id;
  exception
    when exclusion_violation then
      raise exception 'Agent already has an appointment overlapping this time slot.'
        using errcode = 'P0004';
  end;

  return v_appointment_id;
end;
$$;

comment on function public.book_site_visit is
  'Atomically books a site visit. '
  'Double-booking prevention relies on EXCLUDE USING gist constraint (excl_appointments_agent_no_overlap). '
  'Advisory lock is a fast pre-check only. '
  'All tenant references are validated before INSERT.';
