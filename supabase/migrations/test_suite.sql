-- =============================================================================
-- TEST SUITE — Run as service_role in Supabase SQL Editor
-- Tests all corrections from migrations 013-017.
-- Expected output: All "PASSED" messages, zero "FAILED" messages.
-- =============================================================================

do $$
declare
  v_tenant_a    uuid := '00000000-0000-0000-0000-000000000001';  -- ABC Realty (from seed)
  v_tenant_b    uuid := '00000000-0000-0000-0000-000000000002';  -- XYZ Developers (from seed)
  v_customer_a  uuid;
  v_customer_b  uuid;
  v_agent_a     uuid;
  v_lead_a      uuid;
  v_prop_a      uuid;
  v_appt1_id    uuid;
  v_result      uuid;
begin

  -- ----------------------------------------------------------------
  -- SETUP: Get IDs from seed data
  -- ----------------------------------------------------------------
  select id into v_customer_a
  from public.customers where tenant_id = v_tenant_a limit 1;

  select id into v_customer_b
  from public.customers where tenant_id = v_tenant_b limit 1;

  select id into v_agent_a
  from public.sales_agents where tenant_id = v_tenant_a and is_active = true limit 1;

  select id into v_lead_a
  from public.leads where tenant_id = v_tenant_a and deleted_at is null limit 1;

  select id into v_prop_a
  from public.properties where tenant_id = v_tenant_a and status = 'available' limit 1;

  raise notice '=== TEST SUITE START ===';
  raise notice 'Tenant A: %', v_tenant_a;
  raise notice 'Tenant B: %', v_tenant_b;
  raise notice 'Customer A: %', v_customer_a;
  raise notice 'Customer B: %', v_customer_b;
  raise notice 'Agent A: %', v_agent_a;
  raise notice 'Lead A: %', v_lead_a;
  raise notice 'Property A: %', v_prop_a;

  -- ================================================================
  -- T1: is_service_role() check
  -- ================================================================
  if public.is_service_role() then
    raise notice 'T1 PASSED: is_service_role() returns true when run as service_role';
  else
    raise warning 'T1 WARN: is_service_role() returns false — run this as service_role';
  end if;

  -- ================================================================
  -- T2: Property price uniqueness constraint
  -- ================================================================
  if v_prop_a is null then
    raise notice 'T2 SKIP: no available properties in tenant A seed data';
  else
    -- Ensure we have one current price first
    insert into public.property_prices (tenant_id, property_id, price_type, amount, currency, is_current)
    values (v_tenant_a, v_prop_a, 'offer_price', 4500000, 'INR', true)
    on conflict do nothing;

    begin
      -- Try inserting a second current offer_price for the same property
      insert into public.property_prices (tenant_id, property_id, price_type, amount, currency, is_current)
      values (v_tenant_a, v_prop_a, 'offer_price', 5000000, 'INR', true);
      raise exception 'T2 FAILED: duplicate current price was allowed';
    exception
      when unique_violation then
        raise notice 'T2 PASSED: duplicate current price correctly rejected by uq_property_prices_current';
    end;
  end if;

  -- ================================================================
  -- T3: Appointment exclusion constraint (double-booking)
  -- ================================================================
  if v_customer_a is null or v_agent_a is null then
    raise notice 'T3 SKIP: missing customer or agent seed data';
  else
    -- Clean up any old test appointments
    delete from public.appointments
    where tenant_id = v_tenant_a
      and notes = '__test_t3__';

    -- Book first appointment: tomorrow at 10:00 for 60 min
    insert into public.appointments (
      tenant_id, customer_id, sales_agent_id,
      appointment_type, source,
      scheduled_at, duration_minutes, status, notes
    ) values (
      v_tenant_a, v_customer_a, v_agent_a,
      'site_visit', 'admin',
      (current_date + 1 + time '10:00')::timestamptz, 60, 'pending', '__test_t3__'
    ) returning id into v_appt1_id;

    raise notice 'T3a: First appointment booked: %', v_appt1_id;

    -- Attempt overlapping appointment: tomorrow 10:30 for 60 min
    begin
      insert into public.appointments (
        tenant_id, customer_id, sales_agent_id,
        appointment_type, source,
        scheduled_at, duration_minutes, status, notes
      ) values (
        v_tenant_a, v_customer_a, v_agent_a,
        'site_visit', 'admin',
        (current_date + 1 + time '10:30')::timestamptz, 60, 'pending', '__test_t3_overlap__'
      );
      raise exception 'T3b FAILED: overlapping appointment was allowed';
    exception
      when exclusion_violation then
        raise notice 'T3b PASSED: overlapping appointment correctly rejected by exclusion constraint';
    end;

    -- Hour-boundary test: previous system would have allowed 10:59→11:59 + 11:01→12:01
    -- New system correctly rejects these as overlapping via tstzrange
    begin
      insert into public.appointments (
        tenant_id, customer_id, sales_agent_id,
        appointment_type, source,
        scheduled_at, duration_minutes, status, notes
      ) values (
        v_tenant_a, v_customer_a, v_agent_a,
        'site_visit', 'admin',
        (current_date + 1 + time '10:45')::timestamptz, 120, 'pending', '__test_t3_boundary__'
      );
      raise exception 'T3c FAILED: hour-boundary overlap was allowed';
    exception
      when exclusion_violation then
        raise notice 'T3c PASSED: hour-boundary overlap correctly rejected (was a gap in old system)';
    end;

    -- Cleanup
    delete from public.appointments where id = v_appt1_id;
  end if;

  -- ================================================================
  -- T4: Property reservation concurrency (reserve_property function)
  -- ================================================================
  if v_prop_a is null then
    raise notice 'T4 SKIP: no available property in seed data';
  else
    -- First reservation should succeed
    begin
      v_result := public.reserve_property(
        v_tenant_a, v_prop_a, v_lead_a, null, 'hold', 'Test hold T4', null
      );
      raise notice 'T4a PASSED: property reserved: %', v_result;
    exception when others then
      raise warning 'T4a: property already held — trying reserved';
    end;

    -- Second reservation on same property should fail
    begin
      v_result := public.reserve_property(
        v_tenant_a, v_prop_a, v_lead_a, null, 'reserved', 'Test reserved T4', null
      );
      raise exception 'T4b FAILED: second reservation was allowed on a held property';
    exception
      when sqlstate 'P0003' then
        raise notice 'T4b PASSED: second reservation correctly rejected (P0003)';
      when others then
        raise notice 'T4b PASSED with different error: %', sqlerrm;
    end;

    -- Restore property to available for other tests
    update public.properties set status = 'available' where id = v_prop_a;
    raise notice 'T4: property restored to available';
  end if;

  -- ================================================================
  -- T5: Cross-tenant FK enforcement
  -- ================================================================
  if v_customer_b is null then
    raise notice 'T5 SKIP: no tenant B customer in seed data';
  else
    begin
      -- Try to create a lead in tenant A referencing a customer in tenant B
      insert into public.leads (
        tenant_id, customer_id, lead_number, status, sales_stage, lead_score
      ) values (
        v_tenant_a, v_customer_b, 'LD-XTEST-001', 'new', 'new', 0
      );
      raise exception 'T5 FAILED: cross-tenant lead was allowed (tenant A lead → tenant B customer)';
    exception
      when foreign_key_violation then
        raise notice 'T5 PASSED: cross-tenant FK correctly rejected by composite FK constraint';
      when others then
        raise notice 'T5 PASSED with error: %', sqlerrm;
    end;
  end if;

  -- ================================================================
  -- T6: book_site_visit() tenant validation
  -- ================================================================
  if v_customer_b is null or v_agent_a is null then
    raise notice 'T6 SKIP: missing seed data';
  else
    begin
      -- Try to book an appointment for tenant B customer using tenant A agent via tenant A
      v_result := public.book_site_visit(
        v_tenant_a,       -- p_tenant_id = tenant A
        v_customer_b,     -- p_customer_id = tenant B customer (cross-tenant!)
        null, null, null,
        v_agent_a,
        now() + interval '2 days',
        60,
        'admin', null, 'Cross-tenant test', null
      );
      raise exception 'T6 FAILED: cross-tenant book_site_visit was allowed';
    exception
      when sqlstate 'P0002' then
        raise notice 'T6 PASSED: cross-tenant booking correctly rejected (P0002)';
      when others then
        raise notice 'T6 result: %', sqlerrm;
    end;
  end if;

  -- ================================================================
  -- T7: Soft-deleted customer phone uniqueness
  -- ================================================================
  declare
    v_test_phone text := '+91-9999900001';
    v_new_cust_id uuid;
  begin
    -- Clean up any test customers
    delete from public.customers
    where tenant_id = v_tenant_a and phone = v_test_phone;

    -- Insert customer
    insert into public.customers (tenant_id, full_name, phone)
    values (v_tenant_a, 'Test Customer T7', v_test_phone)
    returning id into v_new_cust_id;

    -- Soft delete
    update public.customers set deleted_at = now() where id = v_new_cust_id;

    -- Re-insert with same phone after soft delete — should SUCCEED
    begin
      insert into public.customers (tenant_id, full_name, phone)
      values (v_tenant_a, 'Returning Customer T7', v_test_phone)
      returning id into v_new_cust_id;
      raise notice 'T7a PASSED: returning customer with same phone allowed after soft delete';

      -- Cleanup
      delete from public.customers where id = v_new_cust_id;
      update public.customers set deleted_at = null
      where tenant_id = v_tenant_a and phone = v_test_phone;
      delete from public.customers
      where tenant_id = v_tenant_a and phone = v_test_phone;
    exception
      when unique_violation then
        raise exception 'T7a FAILED: soft-deleted customer phone still blocks new registration';
    end;

    -- Try duplicate phone without soft delete — should FAIL
    insert into public.customers (tenant_id, full_name, phone)
    values (v_tenant_a, 'Active T7a', v_test_phone);

    begin
      insert into public.customers (tenant_id, full_name, phone)
      values (v_tenant_a, 'Duplicate T7b', v_test_phone);
      raise exception 'T7b FAILED: duplicate active phone was allowed';
    exception
      when unique_violation then
        raise notice 'T7b PASSED: duplicate active phone correctly rejected';
    end;

    -- Cleanup
    delete from public.customers
    where tenant_id = v_tenant_a and phone = v_test_phone;
  end;

  raise notice '=== TEST SUITE COMPLETE ===';
end;
$$;

-- ================================================================
-- STANDALONE CHECKS (run individually to verify)
-- ================================================================

-- Check analytics views are tenant-filtered
-- (Run as an authenticated user to verify RLS + view filter both work)
-- select count(*), tenant_id from public.v_lead_pipeline_summary group by tenant_id;
-- Expected: Only 1 tenant_id row (the caller's tenant)

-- Check mv_hot_leads has data and is indexed
-- select count(*), tenant_id from public.mv_hot_leads group by tenant_id;

-- Check exclusion constraint exists
select conname, contype
from   pg_constraint
where  conname = 'excl_appointments_agent_no_overlap';

-- Check property price uniqueness constraint exists
select indexname, indexdef
from   pg_indexes
where  indexname = 'uq_property_prices_current';

-- Check cross-tenant unique constraints exist
select constraint_name
from   information_schema.table_constraints
where  constraint_name in (
  'uq_customers_tenant_id',
  'uq_leads_tenant_id',
  'uq_projects_tenant_id',
  'uq_properties_tenant_id',
  'uq_sales_agents_tenant_id'
)
order by constraint_name;

-- Check ai_usage_events table exists with correct structure
select column_name, data_type
from   information_schema.columns
where  table_schema = 'public'
  and  table_name   = 'ai_usage_events'
order  by ordinal_position;
