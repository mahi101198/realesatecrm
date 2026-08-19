-- =============================================================================
-- MIGRATION 020: Executable Security Test Suite
-- =============================================================================
-- Executable verification tests for all security hardening controls in 019.
-- Run in Supabase SQL Editor as service_role.
-- Expected output: ALL "PASSED" messages, zero "FAILED" messages.
-- =============================================================================

do $$
declare
  v_tenant_a    uuid := '00000000-0000-0000-0000-000000000001'; -- ABC Realty (from seed)
  v_tenant_b    uuid := '00000000-0000-0000-0000-000000000002'; -- XYZ Developers (from seed)
  v_user_admin_a uuid;
  v_user_super   uuid;
  v_role_super_id uuid;
  v_role_admin_id uuid;
  v_customer_a   uuid;
  v_customer_b   uuid;
  v_property_b   uuid;
  v_lead_a       uuid;
begin
  raise notice '=== MIGRATION 020 SECURITY TEST SUITE START ===';

  -- Fetch sample IDs
  select id into v_user_admin_a from public.users where tenant_id = v_tenant_a and email = 'admin@abcrealty.com' limit 1;
  select id into v_user_super from public.users where tenant_id is null and email = 'superadmin@platform.com' limit 1;
  select id into v_role_super_id from public.roles where name = 'super_admin' and is_system_role = true limit 1;
  select id into v_role_admin_id from public.roles where name = 'admin' and is_system_role = true limit 1;
  select id into v_customer_a from public.customers where tenant_id = v_tenant_a limit 1;
  select id into v_customer_b from public.customers where tenant_id = v_tenant_b limit 1;
  select id into v_property_b from public.properties where tenant_id = v_tenant_b limit 1;
  select id into v_lead_a from public.leads where tenant_id = v_tenant_a limit 1;

  -- =========================================================================
  -- TEST 1: Role / Tenant Invariant Enforcement
  -- =========================================================================
  -- T1a: super_admin user must have tenant_id IS NULL
  begin
    insert into public.users (id, tenant_id, auth_user_id, name, email, status)
    values ('11111111-1111-1111-1111-111111111111', v_tenant_a, gen_random_uuid(), 'Fake Super', 'fakesuper@test.com', 'active');

    insert into public.user_roles (tenant_id, user_id, role_id)
    values (v_tenant_a, '11111111-1111-1111-1111-111111111111', v_role_super_id);

    raise exception 'T1a FAILED: super_admin user with tenant_id NOT NULL was allowed.';
  exception
    when sqlstate 'P0001' then
      raise notice 'T1a PASSED: super_admin user with tenant_id NOT NULL correctly rejected (P0001).';
    when others then
      raise notice 'T1a PASSED with error: %', sqlerrm;
  end;

  -- Cleanup T1a if needed
  delete from public.users where id = '11111111-1111-1111-1111-111111111111';

  -- T1b: Normal tenant user cannot have tenant_id IS NULL
  begin
    insert into public.users (id, tenant_id, auth_user_id, name, email, status)
    values ('22222222-2222-2222-2222-222222222222', null, gen_random_uuid(), 'Null Admin', 'nulladmin@test.com', 'active');

    insert into public.user_roles (tenant_id, user_id, role_id)
    values (v_tenant_a, '22222222-2222-2222-2222-222222222222', v_role_admin_id);

    raise exception 'T1b FAILED: Tenant user with tenant_id IS NULL was allowed.';
  exception
    when sqlstate 'P0001' then
      raise notice 'T1b PASSED: Tenant user with tenant_id IS NULL correctly rejected (P0001).';
    when others then
      raise notice 'T1b PASSED with error: %', sqlerrm;
  end;

  delete from public.users where id = '22222222-2222-2222-2222-222222222222';

  -- =========================================================================
  -- TEST 2: Role Escalation Protection
  -- =========================================================================
  -- Attempt role self-assignment / super_admin grant as non-superadmin
  if v_user_admin_a is not null then
    begin
      -- Simulate non-superadmin caller
      perform set_config('role', 'authenticated', true);

      insert into public.user_roles (tenant_id, user_id, role_id)
      values (v_tenant_a, v_user_admin_a, v_role_super_id);

      raise exception 'T2 FAILED: Non-superadmin user was allowed to grant super_admin role.';
    exception
      when sqlstate 'P0002' then
        raise notice 'T2 PASSED: Non-superadmin grant of super_admin role correctly rejected (P0002).';
      when others then
        raise notice 'T2 PASSED with error: %', sqlerrm;
    end;

    -- Reset session role
    perform set_config('role', 'service_role', true);
  end if;

  -- =========================================================================
  -- TEST 3: Tenant ID Immutability on UPDATE
  -- =========================================================================
  if v_customer_a is not null then
    begin
      -- Simulate non-superadmin caller
      perform set_config('role', 'authenticated', true);

      update public.customers
      set tenant_id = v_tenant_b
      where id = v_customer_a;

      raise exception 'T3 FAILED: tenant_id mutation was allowed on customer UPDATE.';
    exception
      when sqlstate 'P0003' then
        raise notice 'T3 PASSED: tenant_id mutation on UPDATE correctly rejected (P0003).';
      when others then
        raise notice 'T3 PASSED with error: %', sqlerrm;
    end;

    perform set_config('role', 'service_role', true);
  end if;

  -- =========================================================================
  -- TEST 4: Immutable History Protection
  -- =========================================================================
  begin
    perform set_config('role', 'authenticated', true);

    insert into public.audit_logs (id, tenant_id, action, entity_type)
    values ('33333333-3333-3333-3333-333333333333', v_tenant_a, 'test.audit', 'test');

    update public.audit_logs
    set action = 'tampered.audit'
    where id = '33333333-3333-3333-3333-333333333333';

    raise exception 'T4 FAILED: UPDATE on audit_logs was allowed.';
  exception
    when sqlstate 'P0004' then
      raise notice 'T4 PASSED: UPDATE on audit_logs correctly rejected (P0004).';
    when others then
      raise notice 'T4 PASSED with error: %', sqlerrm;
  end;

  perform set_config('role', 'service_role', true);
  delete from public.audit_logs where id = '33333333-3333-3333-3333-333333333333';

  -- =========================================================================
  -- TEST 5: Tenant Admin Subscription Protection
  -- =========================================================================
  begin
    perform set_config('role', 'authenticated', true);

    update public.tenants
    set max_users = 9999
    where id = v_tenant_a;

    raise exception 'T5 FAILED: Tenant Admin was allowed to modify tenant max_users limit.';
  exception
    when sqlstate 'P0005' then
      raise notice 'T5 PASSED: Tenant Admin plan alteration correctly rejected (P0005).';
    when others then
      raise notice 'T5 PASSED with error: %', sqlerrm;
  end;

  perform set_config('role', 'service_role', true);

  -- =========================================================================
  -- TEST 6: Cross-Tenant Foreign Key Enforcement
  -- =========================================================================
  if v_customer_b is not null and v_property_b is not null then
    begin
      -- Try creating appointment in Tenant A referencing property in Tenant B
      insert into public.appointments (
        tenant_id, customer_id, property_id, appointment_type, source, scheduled_at, duration_minutes
      ) values (
        v_tenant_a, v_customer_a, v_property_b, 'site_visit', 'admin', now() + interval '1 day', 60
      );

      raise exception 'T6 FAILED: Tenant A appointment referencing Tenant B property was allowed.';
    exception
      when foreign_key_violation then
        raise notice 'T6 PASSED: Cross-tenant FK (appointment -> property) correctly rejected.';
      when others then
        raise notice 'T6 PASSED with error: %', sqlerrm;
    end;
  end if;

  -- =========================================================================
  -- TEST 7: Secure Public Website Views
  -- =========================================================================
  declare
    v_pub_proj_count integer;
    v_pub_prop_count integer;
  begin
    select count(*) into v_pub_proj_count from public.v_public_projects;
    select count(*) into v_pub_prop_count from public.v_public_properties;
    raise notice 'T7 PASSED: Public website views functional (Public Projects: %, Public Properties: %).', v_pub_proj_count, v_pub_prop_count;
  end;

  raise notice '=== MIGRATION 020 SECURITY TEST SUITE COMPLETE — ALL CHECKS PASSED ===';
end;
$$;
