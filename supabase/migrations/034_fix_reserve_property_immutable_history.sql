-- =============================================================================
-- MIGRATION 034: Fix reserve_property() vs. immutable history trigger
--
-- Bug: migration 019 (final security hardening) attached
-- trg_fn_enforce_immutable_history to property_status_history, forbidding any
-- UPDATE/DELETE on that table outside service_role. But public.reserve_property
-- (migration 014) annotates the history row it just triggered by doing exactly
-- that forbidden UPDATE afterwards (to backfill reason/related_lead_id/
-- related_call_id, since the INSERT itself is done by a separate trigger that
-- didn't know those values). The result: every reserve/hold call has been
-- raising "IMMUTABILITY VIOLATION: Historical log records cannot be updated or
-- deleted." since 019 landed, which app/properties/service.py.reserve_property
-- (correctly, but misleadingly) reports to the caller as "Property is no
-- longer available or is being reserved by another request." -- this affected
-- every reservation attempt, not just ones with a lead_id/reason set.
--
-- test_suite.sql's T4a masked this: it wraps reserve_property in a bare
-- "exception when others" that only raise warnings, so the regression never
-- failed CI.
--
-- Fix, mirroring the pattern migration 025 already uses for
-- trg_fn_property_type_change (reads actor from a SET LOCAL session var at
-- INSERT time instead of updating after the fact): reserve_property now sets
-- session-local config vars for reason/lead/call BEFORE updating
-- properties.status (which is what fires the INSERT trigger), and
-- trg_fn_property_status_change reads them at INSERT time. The illegal
-- post-insert UPDATE is removed entirely.
-- =============================================================================

create or replace function public.trg_fn_property_status_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor_user_id  uuid;
  v_reason         text;
  v_related_lead   uuid;
  v_related_call   uuid;
  v_changed_by_type text;
begin
  if old.status is distinct from new.status then
    -- Read context set by reserve_property() (or left unset by a plain
    -- properties UPDATE elsewhere, e.g. sale/cancel flows) -- same idiom as
    -- trg_fn_property_type_change in migration 025.
    begin
      v_actor_user_id := nullif(current_setting('app.current_user_id', true), '')::uuid;
    exception when others then
      v_actor_user_id := null;
    end;

    v_reason := nullif(current_setting('app.status_change_reason', true), '');

    begin
      v_related_lead := nullif(current_setting('app.status_change_lead_id', true), '')::uuid;
    exception when others then
      v_related_lead := null;
    end;

    begin
      v_related_call := nullif(current_setting('app.status_change_call_id', true), '')::uuid;
    exception when others then
      v_related_call := null;
    end;

    v_changed_by_type := case when v_actor_user_id is not null then 'user' else 'system' end;

    insert into public.property_status_history (
      tenant_id, property_id,
      previous_status, new_status,
      reason, changed_by_user_id, changed_by_type,
      related_lead_id, related_call_id
    ) values (
      new.tenant_id, new.id,
      old.status, new.status,
      v_reason, v_actor_user_id, v_changed_by_type,
      v_related_lead, v_related_call
    );

    -- Session vars are set with is_local=true (see reserve_property) so they
    -- fall away at transaction end regardless, but clear them explicitly too
    -- so a later status change later in the SAME transaction (unusual, but
    -- possible via direct SQL) doesn't inherit stale reason/lead/call.
    perform set_config('app.status_change_reason', '', true);
    perform set_config('app.status_change_lead_id', '', true);
    perform set_config('app.status_change_call_id', '', true);
  end if;
  return new;
end;
$$;

comment on function public.trg_fn_property_status_change() is
  'Records property.status changes in property_status_history. Reads reason/'
  'related_lead_id/related_call_id/actor from SET LOCAL session vars (set by '
  'reserve_property or left blank by other callers) at INSERT time -- history '
  'rows are never updated afterwards, since migration 019 made this table '
  'strictly append-only.';

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

  -- Set session variables for the INSERT trigger (trg_fn_property_status_change)
  -- to pick up -- property_status_history is append-only as of migration 019,
  -- so these can no longer be backfilled via a follow-up UPDATE.
  perform set_config('app.current_user_id', coalesce(p_actor_user_id::text, ''), true);
  perform set_config('app.status_change_reason', coalesce(p_reason, ''), true);
  perform set_config('app.status_change_lead_id', coalesce(p_lead_id::text, ''), true);
  perform set_config('app.status_change_call_id', coalesce(p_call_id::text, ''), true);

  -- Update status (triggers property_status_history insert, already annotated).
  update public.properties
  set    status = p_new_status
  where  id = p_property_id;

  return p_property_id;
end;
$$;

comment on function public.reserve_property is
  'Atomically reserves or holds a property using SELECT FOR UPDATE row locking. '
  'Prevents concurrent reservation of the same property. Annotates the '
  'resulting property_status_history row via SET LOCAL session vars read by '
  'trg_fn_property_status_change, since that table is append-only.';
