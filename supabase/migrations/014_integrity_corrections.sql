-- =============================================================================
-- MIGRATION 014: Integrity Corrections
--
-- Fixes:
-- 1. Property price current-price uniqueness (HIGH)
-- 2. is_service_role() implementation (HIGH)
-- 3. Soft-deleted customer phone uniqueness (MEDIUM)
-- 4. Cross-tenant FK safety on critical paths (HIGH)
-- 5. Property status history: actor capture (HIGH)
-- 6. Property price history: actor capture (HIGH)
-- 7. Property reservation function (MEDIUM)
-- 8. Agent config snapshot on calls (MEDIUM)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. PROPERTY PRICE CURRENT-PRICE UNIQUENESS
--    Prevents two concurrent INSERTs creating two is_current=true rows for
--    the same (property_id, price_type). The existing trigger expires old
--    rows but cannot prevent a race condition between two concurrent inserts.
-- ---------------------------------------------------------------------------

-- Clean up any existing duplicates before adding the constraint
with ranked as (
  select id,
         row_number() over (
           partition by property_id, price_type
           order by created_at desc
         ) as rn
  from public.property_prices
  where is_current = true
)
update public.property_prices
set    is_current    = false,
       effective_until = current_date
where  id in (select id from ranked where rn > 1);

create unique index if not exists uq_property_prices_current
  on public.property_prices (property_id, price_type)
  where is_current = true;

comment on index public.uq_property_prices_current is
  'Exactly one current price per price_type per property. '
  'Prevents concurrent INSERT race even with the expire trigger.';

-- ---------------------------------------------------------------------------
-- 2. FIX is_service_role()
--    current_setting(''role'', true) is unreliable in Supabase Postgres.
--    Use current_user which is the actual PostgreSQL role.
-- ---------------------------------------------------------------------------

create or replace function public.is_service_role()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select current_user = 'service_role';
$$;

comment on function public.is_service_role() is
  'Returns TRUE when the current session is running as the service_role '
  'PostgreSQL role. Uses current_user (reliable) not current_setting(''role'').';

-- ---------------------------------------------------------------------------
-- 3. SOFT-DELETED CUSTOMER PHONE UNIQUENESS
--    The hard UNIQUE constraint blocks re-registration of a returning customer
--    whose old record is soft-deleted. Replace with a partial unique index.
-- ---------------------------------------------------------------------------

-- Drop old hard constraint
alter table public.customers
  drop constraint if exists uq_customers_tenant_phone;

-- Drop old plain index
drop index if exists public.idx_customers_tenant_phone;

-- Partial unique: unique only among non-deleted customers
create unique index if not exists uq_customers_tenant_phone_active
  on public.customers (tenant_id, phone)
  where deleted_at is null;

-- General index for lookup (without uniqueness filter)
create index if not exists idx_customers_tenant_phone
  on public.customers (tenant_id, phone);

comment on index public.uq_customers_tenant_phone_active is
  'Phone numbers are unique per tenant among non-soft-deleted customers. '
  'Allows re-registration of returning customers after soft delete.';

-- ---------------------------------------------------------------------------
-- 4. CROSS-TENANT FK SAFETY
--    Simple FK (child.customer_id → customers.id) cannot prevent a row in
--    tenant A from pointing to a customer in tenant B because the FK only
--    checks id, not (tenant_id, id).
--
--    Solution: Add composite UNIQUE on parent tables, then add composite
--    FKs on child tables for the most critical relationships.
--    This makes cross-tenant references a FK violation, not just an RLS issue.
-- ---------------------------------------------------------------------------

-- Add composite unique keys on parent tables
-- (safe to add even if data exists, as (tenant_id, id) is already unique by design)

alter table public.customers
  add constraint uq_customers_tenant_id
  unique (tenant_id, id);

alter table public.leads
  add constraint uq_leads_tenant_id
  unique (tenant_id, id);

alter table public.projects
  add constraint uq_projects_tenant_id
  unique (tenant_id, id);

alter table public.properties
  add constraint uq_properties_tenant_id
  unique (tenant_id, id);

alter table public.sales_agents
  add constraint uq_sales_agents_tenant_id
  unique (tenant_id, id);

-- ---- follow_ups: composite FK for customer and lead ----

-- Drop old simple FKs
alter table public.follow_ups
  drop constraint if exists follow_ups_customer_id_fkey,
  drop constraint if exists follow_ups_lead_id_fkey;

-- Add composite FKs
alter table public.follow_ups
  add constraint fk_follow_ups_customer_tenant
  foreign key (tenant_id, customer_id)
  references public.customers (tenant_id, id)
  on delete restrict;

alter table public.follow_ups
  add constraint fk_follow_ups_lead_tenant
  foreign key (tenant_id, lead_id)
  references public.leads (tenant_id, id)
  on delete cascade;

-- ---- appointments: composite FK for customer, lead, project ----

alter table public.appointments
  drop constraint if exists appointments_customer_id_fkey,
  drop constraint if exists appointments_lead_id_fkey,
  drop constraint if exists appointments_project_id_fkey;

alter table public.appointments
  add constraint fk_appointments_customer_tenant
  foreign key (tenant_id, customer_id)
  references public.customers (tenant_id, id)
  on delete restrict;

alter table public.appointments
  add constraint fk_appointments_lead_tenant
  foreign key (tenant_id, lead_id)
  references public.leads (tenant_id, id)
  on delete set null;

alter table public.appointments
  add constraint fk_appointments_project_tenant
  foreign key (tenant_id, project_id)
  references public.projects (tenant_id, id)
  on delete set null;

-- ---- calls: composite FK for customer and lead ----

alter table public.calls
  drop constraint if exists calls_customer_id_fkey,
  drop constraint if exists calls_lead_id_fkey;

alter table public.calls
  add constraint fk_calls_customer_tenant
  foreign key (tenant_id, customer_id)
  references public.customers (tenant_id, id)
  on delete restrict;

alter table public.calls
  add constraint fk_calls_lead_tenant
  foreign key (tenant_id, lead_id)
  references public.leads (tenant_id, id)
  on delete set null;

-- ---- lead_property_interests: composite FK for lead and project ----

alter table public.lead_property_interests
  drop constraint if exists lead_property_interests_lead_id_fkey,
  drop constraint if exists lead_property_interests_project_id_fkey;

alter table public.lead_property_interests
  add constraint fk_lead_prop_int_lead_tenant
  foreign key (tenant_id, lead_id)
  references public.leads (tenant_id, id)
  on delete cascade;

alter table public.lead_property_interests
  add constraint fk_lead_prop_int_project_tenant
  foreign key (tenant_id, project_id)
  references public.projects (tenant_id, id)
  on delete restrict;

-- ---------------------------------------------------------------------------
-- 5. PROPERTY STATUS HISTORY — Actor capture
--    The trigger previously always recorded changed_by_type = 'system' and
--    never captured the user_id. FastAPI should set:
--    SET LOCAL app.current_user_id = '<user_uuid>';
--    before any property status UPDATE.
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_property_status_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor_user_id  uuid;
  v_actor_type     text;
begin
  if old.status is distinct from new.status then
    -- Try to read actor from session variable set by FastAPI
    begin
      v_actor_user_id := nullif(current_setting('app.current_user_id', true), '')::uuid;
    exception when others then
      v_actor_user_id := null;
    end;

    v_actor_type := case
      when v_actor_user_id is not null then 'user'
      else 'system'
    end;

    insert into public.property_status_history (
      tenant_id,
      property_id,
      previous_status,
      new_status,
      changed_by_user_id,
      changed_by_type
    ) values (
      new.tenant_id,
      new.id,
      old.status,
      new.status,
      v_actor_user_id,
      v_actor_type
    );
  end if;
  return new;
end;
$$;

comment on function public.trg_fn_property_status_change() is
  'Records property status transitions in property_status_history. '
  'Reads actor from SET LOCAL app.current_user_id if set by FastAPI.';

-- ---------------------------------------------------------------------------
-- 6. PROPERTY PRICE HISTORY — Actor capture
--    Rewrite trg_fn_expire_old_price to:
--    a) Capture old amount correctly (before expiring it)
--    b) Capture actor from session variable
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_expire_old_price()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_old_amount     numeric(15, 2);
  v_actor_user_id  uuid;
begin
  if new.is_current = true then
    -- Capture old amount BEFORE expiring it
    select amount into v_old_amount
    from   public.property_prices
    where  property_id = new.property_id
      and  price_type  = new.price_type
      and  is_current  = true
      and  id          <> new.id
    order  by effective_from desc
    limit  1;

    -- Expire the old current price
    update public.property_prices
    set    is_current     = false,
           effective_until = new.effective_from - interval '1 day'
    where  property_id = new.property_id
      and  price_type  = new.price_type
      and  is_current  = true
      and  id          <> new.id;

    -- Read actor from session variable
    begin
      v_actor_user_id := nullif(current_setting('app.current_user_id', true), '')::uuid;
    exception when others then
      v_actor_user_id := null;
    end;

    -- Record price history
    insert into public.property_price_history (
      tenant_id,
      property_id,
      property_price_id,
      price_type,
      old_amount,
      new_amount,
      currency,
      changed_by
    ) values (
      new.tenant_id,
      new.property_id,
      new.id,
      new.price_type,
      v_old_amount,
      new.amount,
      new.currency,
      v_actor_user_id
    );
  end if;
  return new;
end;
$$;

comment on function public.trg_fn_expire_old_price() is
  'Expires old current price and records change in property_price_history. '
  'Correctly captures old_amount before expiry. Reads actor from session variable.';

-- ---------------------------------------------------------------------------
-- 7. PROPERTY RESERVATION FUNCTION
--    Atomic conditional status transition with SELECT FOR UPDATE.
--    FastAPI MUST call this inside an explicit transaction.
--    Pattern: SELECT FOR UPDATE → check status → UPDATE → return.
-- ---------------------------------------------------------------------------

create or replace function public.reserve_property(
  p_tenant_id      uuid,
  p_property_id    uuid,
  p_lead_id        uuid,       -- Optional: link to a lead
  p_call_id        uuid,       -- Optional: link to a call
  p_new_status     public.property_status,   -- 'hold' or 'reserved'
  p_reason         text,
  p_actor_user_id  uuid        -- Optional: who is reserving
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_current_status  public.property_status;
  v_current_tenant  uuid;
begin
  -- Validate target status
  if p_new_status not in ('hold', 'reserved') then
    raise exception 'Invalid reservation status: %. Use ''hold'' or ''reserved''.', p_new_status
      using errcode = 'P0001';
  end if;

  -- Lock the specific property row for update.
  -- Only one concurrent transaction can hold this lock.
  -- Other transactions attempting the same property will block (not fail).
  select status, tenant_id
  into   v_current_status, v_current_tenant
  from   public.properties
  where  id = p_property_id
  for    update;

  if not found then
    raise exception 'Property % not found.', p_property_id
      using errcode = 'P0001';
  end if;

  -- Validate tenant ownership
  if v_current_tenant <> p_tenant_id then
    raise exception 'Property does not belong to tenant.'
      using errcode = 'P0002';
  end if;

  -- Only 'available' properties can be reserved
  if v_current_status <> 'available' then
    raise exception 'Property is not available. Current status: %.', v_current_status
      using errcode = 'P0003';
  end if;

  -- Set session variable for actor capture in status history trigger
  perform set_config('app.current_user_id', coalesce(p_actor_user_id::text, ''), true);

  -- Update status (triggers property_status_history insert)
  update public.properties
  set    status = p_new_status
  where  id = p_property_id;

  -- Annotate the status history record with lead/call context
  -- (trigger already inserted the row; we update reason and related IDs)
  update public.property_status_history
  set    reason          = p_reason,
         related_lead_id = p_lead_id,
         related_call_id = p_call_id
  where  property_id = p_property_id
    and  new_status  = p_new_status
    and  created_at  >= now() - interval '10 seconds'
    and  reason is null;  -- Only update if trigger didn't already set reason

  return p_property_id;
end;
$$;

comment on function public.reserve_property is
  'Atomically reserves or holds a property using SELECT FOR UPDATE row locking. '
  'Prevents concurrent reservation of the same property. '
  'Requires an explicit PostgreSQL transaction from the caller. '
  'Only succeeds if current status = ''available''.';

-- ---------------------------------------------------------------------------
-- 8. AGENT CONFIG SNAPSHOT ON CALLS
--    Captures key model identifiers at call start so historical calls remain
--    accurately attributed even if the agent_configs record is later modified.
--    FastAPI should populate this when initiating a call.
-- ---------------------------------------------------------------------------

alter table public.calls
  add column if not exists agent_config_snapshot jsonb not null default '{}';

comment on column public.calls.agent_config_snapshot is
  'Point-in-time snapshot of agent config at call start. '
  'Example: {"version":1,"stt_model":"nova-3","llm_model":"gemma-4-31b-it","tts_model":"sonic-3","system_prompt_hash":"abc123"}. '
  'Allows accurate historical attribution even if agent_configs is later modified.';
