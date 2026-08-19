-- =============================================================================
-- MIGRATION 019: Final Database Security Hardening & Integrity Audit
-- =============================================================================
-- Core Security Hardening Pass for Real Estate CRM + AI Voice Agent:
-- 1. Enforce Role / Tenant Invariants (super_admin -> tenant_id IS NULL; normal -> NOT NULL)
-- 2. Prevent Role Escalation (non-super_admin cannot grant super_admin / admin roles)
-- 3. Enforce Tenant ID Immutability on UPDATE across all tenant-owned tables
-- 4. Enforce Hard Immutability on Historical / Audit Tables (prevent UPDATE/DELETE)
-- 5. Restrict Tenant Admin from modifying subscription plan parameters
-- 6. Add Composite Foreign Keys for strict cross-tenant isolation
-- 7. Configure explicit REVOKE, GRANT, and ALTER DEFAULT PRIVILEGES
-- 8. Enable Super Admin INSERT RLS on property/media/history tables
-- 9. Create secure public website views (v_public_projects, v_public_properties)
-- 10. Add extra Integrity CHECK constraints
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. ROLE / TENANT INVARIANT ENFORCEMENT
--    Rule:
--      super_admin  -> users.tenant_id MUST BE NULL
--      admin, manager, sales_manager, sales_agent, viewer -> tenant_id MUST NOT BE NULL
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_enforce_user_role_tenant_invariant()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id   uuid;
  v_tenant_id uuid;
  v_has_super boolean;
  v_has_other boolean;
begin
  -- Determine target user_id based on table
  if TG_TABLE_NAME = 'users' then
    v_user_id := NEW.id;
    v_tenant_id := NEW.tenant_id;
  elsif TG_TABLE_NAME = 'user_roles' then
    v_user_id := NEW.user_id;
    select tenant_id into v_tenant_id from public.users where id = v_user_id;
  end if;

  if v_user_id is null then
    return NEW;
  end if;

  -- Check user roles
  select
    exists (
      select 1 from public.user_roles ur
      join public.roles r on r.id = ur.role_id
      where ur.user_id = v_user_id and r.name = 'super_admin' and r.is_system_role = true and ur.is_active = true
    ),
    exists (
      select 1 from public.user_roles ur
      join public.roles r on r.id = ur.role_id
      where ur.user_id = v_user_id and r.name <> 'super_admin' and ur.is_active = true
    )
  into v_has_super, v_has_other;

  -- Enforce rules
  if v_has_super then
    if v_tenant_id is not null then
      raise exception 'INVARIANT VIOLATION: super_admin users must have tenant_id IS NULL.'
        using errcode = 'P0001';
    end if;
    if v_has_other then
      raise exception 'INVARIANT VIOLATION: super_admin users cannot also have tenant-level roles.'
        using errcode = 'P0001';
    end if;
  elsif v_has_other then
    if v_tenant_id is null then
      raise exception 'INVARIANT VIOLATION: Tenant users must have tenant_id IS NOT NULL.'
        using errcode = 'P0001';
    end if;
  end if;

  return NEW;
end;
$$;

comment on function public.trg_fn_enforce_user_role_tenant_invariant() is
  'Enforces role/tenant invariant: super_admin -> tenant_id IS NULL; normal roles -> tenant_id IS NOT NULL.';

-- Attach to users table
drop trigger if exists trg_users_enforce_role_tenant_invariant on public.users;
create trigger trg_users_enforce_role_tenant_invariant
  after insert or update of tenant_id on public.users
  for each row execute function public.trg_fn_enforce_user_role_tenant_invariant();

-- Attach to user_roles table
drop trigger if exists trg_user_roles_enforce_role_tenant_invariant on public.user_roles;
create trigger trg_user_roles_enforce_role_tenant_invariant
  after insert or update of role_id, is_active on public.user_roles
  for each row execute function public.trg_fn_enforce_user_role_tenant_invariant();

-- ---------------------------------------------------------------------------
-- 2. ROLE ESCALATION PROTECTION
--    Rule:
--      Only super_admin or service_role can grant super_admin role.
--      Only super_admin, service_role, or tenant admin can grant admin role.
--      Users cannot self-assign or elevate their own roles.
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_prevent_role_escalation()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_role_name  text;
  v_caller_id  uuid;
begin
  -- Bypass for service_role
  if public.is_service_role() then
    return coalesce(NEW, OLD);
  end if;

  -- Read target role name
  if TG_OP = 'DELETE' then
    select name into v_role_name from public.roles where id = OLD.role_id;
  else
    select name into v_role_name from public.roles where id = NEW.role_id;
  end if;

  -- Get caller user id
  select id into v_caller_id from public.users where auth_user_id = auth.uid() limit 1;

  -- Prevent self-role modification
  if TG_OP in ('INSERT', 'UPDATE') and NEW.user_id = v_caller_id and not public.is_super_admin() then
    raise exception 'ROLE ESCALATION PREVENTED: Users cannot assign or modify their own roles.'
      using errcode = 'P0002';
  end if;

  -- super_admin role assignment protection
  if v_role_name = 'super_admin' then
    if not public.is_super_admin() then
      raise exception 'ROLE ESCALATION PREVENTED: Only platform super_admin can manage super_admin roles.'
        using errcode = 'P0002';
    end if;
  end if;

  -- admin role assignment protection
  if v_role_name = 'admin' then
    if not (public.is_super_admin() or public.has_permission('user.update')) then
      raise exception 'ROLE ESCALATION PREVENTED: Insufficient permissions to grant admin role.'
        using errcode = 'P0002';
    end if;
  end if;

  return coalesce(NEW, OLD);
end;
$$;

comment on function public.trg_fn_prevent_role_escalation() is
  'Prevents privilege escalation by restricting who can assign super_admin and admin roles.';

drop trigger if exists trg_user_roles_prevent_escalation on public.user_roles;
create trigger trg_user_roles_prevent_escalation
  before insert or update or delete on public.user_roles
  for each row execute function public.trg_fn_prevent_role_escalation();

-- ---------------------------------------------------------------------------
-- 3. TENANT ID IMMUTABILITY ON UPDATE
--    Rule:
--      tenant_id cannot be changed via UPDATE on any tenant-owned table,
--      except by super_admin or service_role.
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_prevent_tenant_id_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if OLD.tenant_id is distinct from NEW.tenant_id then
    if not (public.is_super_admin() or public.is_service_role()) then
      raise exception 'SECURITY VIOLATION: tenant_id is immutable and cannot be modified.'
        using errcode = 'P0003';
    end if;
  end if;
  return NEW;
end;
$$;

comment on function public.trg_fn_prevent_tenant_id_change() is
  'Prevents tenant_id from being altered on UPDATE operations across all tenant tables.';

-- Attach to all tenant-owned tables
do $$
declare
  t text;
  tables text[] := array[
    'users', 'user_roles', 'sales_agents', 'projects', 'properties',
    'property_status_history', 'property_prices', 'property_price_history',
    'project_images', 'property_images', 'property_documents',
    'customers', 'customer_preferences', 'customer_notes', 'leads',
    'lead_property_interests', 'lead_notes', 'campaigns', 'campaign_leads',
    'agent_configs', 'calls', 'call_participants', 'call_messages',
    'call_events', 'agent_sessions', 'conversation_summaries',
    'lead_score_events', 'sales_assignments', 'follow_ups', 'appointments',
    'appointment_history', 'whatsapp_templates', 'whatsapp_messages',
    'communication_logs', 'activities', 'notifications', 'audit_logs',
    'integrations', 'webhook_events', 'ai_usage_events'
  ];
begin
  foreach t in array tables loop
    execute format('drop trigger if exists trg_%I_prevent_tenant_change on public.%I;', t, t);
    execute format('create trigger trg_%I_prevent_tenant_change before update on public.%I for each row execute function public.trg_fn_prevent_tenant_id_change();', t, t);
  end loop;
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. HARD IMMUTABILITY ON HISTORICAL TABLES
--    Rule:
--      UPDATE and DELETE are strictly forbidden on historical tables unless
--      executed by service_role during maintenance.
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_enforce_immutable_history()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_service_role() then
    raise exception 'IMMUTABILITY VIOLATION: Historical log records cannot be updated or deleted.'
      using errcode = 'P0004';
  end if;
  return coalesce(NEW, OLD);
end;
$$;

comment on function public.trg_fn_enforce_immutable_history() is
  'Enforces hard immutability on append-only historical log tables.';

do $$
declare
  t text;
  hist_tables text[] := array[
    'audit_logs', 'property_status_history', 'property_price_history',
    'appointment_history', 'lead_score_events', 'call_messages',
    'call_events', 'activities', 'ai_usage_events'
  ];
begin
  foreach t in array hist_tables loop
    execute format('drop trigger if exists trg_%I_enforce_immutable on public.%I;', t, t);
    execute format('create trigger trg_%I_enforce_immutable before update or delete on public.%I for each row execute function public.trg_fn_enforce_immutable_history();', t, t);
  end loop;
end;
$$;

-- ---------------------------------------------------------------------------
-- 5. TENANT ADMIN SUBSCRIPTION PARAMETER PROTECTION
--    Rule:
--      Tenant Admin can update their own company profile (name, phone, address, logo)
--      in tenants, but CANNOT alter subscription limits (plan, max_users, etc.).
-- ---------------------------------------------------------------------------

-- Update RLS on tenants table to allow tenant admin update on own tenant
drop policy if exists "tenants: admin can update own tenant" on public.tenants;

create policy "tenants: admin can update own tenant"
  on public.tenants for update
  to authenticated
  using (
    public.is_super_admin()
    or (id = public.get_current_tenant_id() and public.has_permission('user.update'))
  )
  with check (
    public.is_super_admin()
    or (id = public.get_current_tenant_id() and public.has_permission('user.update'))
  );

create or replace function public.trg_fn_protect_tenant_plan_settings()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if not (public.is_super_admin() or public.is_service_role()) then
    if OLD.plan is distinct from NEW.plan
       or OLD.plan_expires_at is distinct from NEW.plan_expires_at
       or OLD.max_users is distinct from NEW.max_users
       or OLD.max_properties is distinct from NEW.max_properties
       or OLD.max_ai_minutes is distinct from NEW.max_ai_minutes
       or OLD.is_active is distinct from NEW.is_active
    then
      raise exception 'SECURITY VIOLATION: Subscription and plan settings can only be modified by platform super_admin.'
        using errcode = 'P0005';
    end if;
  end if;
  return NEW;
end;
$$;

drop trigger if exists trg_tenants_protect_plan_settings on public.tenants;
create trigger trg_tenants_protect_plan_settings
  before update on public.tenants
  for each row execute function public.trg_fn_protect_tenant_plan_settings();

-- ---------------------------------------------------------------------------
-- 6. COMPOSITE FOREIGN KEYS FOR CROSS-TENANT ISOLATION
-- ---------------------------------------------------------------------------

-- Composite unique constraints on parent tables
alter table public.campaigns
  add constraint uq_campaigns_tenant_id unique (tenant_id, id);

alter table public.agent_configs
  add constraint uq_agent_configs_tenant_id unique (tenant_id, id);

-- Composite FKs on appointments
alter table public.appointments
  drop constraint if exists appointments_property_id_fkey,
  drop constraint if exists appointments_sales_agent_id_fkey;

alter table public.appointments
  add constraint fk_appointments_property_tenant
  foreign key (tenant_id, property_id) references public.properties (tenant_id, id) on delete set null,
  add constraint fk_appointments_sales_agent_tenant
  foreign key (tenant_id, sales_agent_id) references public.sales_agents (tenant_id, id) on delete set null;

-- Composite FKs on calls
alter table public.calls
  drop constraint if exists calls_campaign_id_fkey,
  drop constraint if exists calls_assigned_sales_agent_id_fkey;

alter table public.calls
  add constraint fk_calls_campaign_tenant
  foreign key (tenant_id, campaign_id) references public.campaigns (tenant_id, id) on delete set null,
  add constraint fk_calls_sales_agent_tenant
  foreign key (tenant_id, assigned_sales_agent_id) references public.sales_agents (tenant_id, id) on delete set null;

-- Composite FK on properties -> projects
alter table public.properties
  drop constraint if exists properties_project_id_fkey;

alter table public.properties
  add constraint fk_properties_project_tenant
  foreign key (tenant_id, project_id) references public.projects (tenant_id, id) on delete restrict;

-- Composite FKs on customer_preferences, customer_notes, lead_notes
alter table public.customer_preferences
  drop constraint if exists customer_preferences_customer_id_fkey;

alter table public.customer_preferences
  add constraint fk_cust_prefs_customer_tenant
  foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete cascade;

alter table public.customer_notes
  drop constraint if exists customer_notes_customer_id_fkey;

alter table public.customer_notes
  add constraint fk_cust_notes_customer_tenant
  foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete cascade;

alter table public.lead_notes
  drop constraint if exists lead_notes_lead_id_fkey;

alter table public.lead_notes
  add constraint fk_lead_notes_lead_tenant
  foreign key (tenant_id, lead_id) references public.leads (tenant_id, id) on delete cascade;

-- Composite FKs on whatsapp_messages, communication_logs, activities
alter table public.whatsapp_messages
  drop constraint if exists whatsapp_messages_customer_id_fkey;

alter table public.whatsapp_messages
  add constraint fk_wa_messages_customer_tenant
  foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete restrict;

alter table public.communication_logs
  drop constraint if exists communication_logs_customer_id_fkey;

alter table public.communication_logs
  add constraint fk_comm_logs_customer_tenant
  foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete restrict;

alter table public.activities
  drop constraint if exists activities_customer_id_fkey;

alter table public.activities
  add constraint fk_activities_customer_tenant
  foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete set null;

-- ---------------------------------------------------------------------------
-- 7. SUPER ADMIN INSERT POLICIES ON MEDIA & PRICE TABLES
-- ---------------------------------------------------------------------------

-- property_prices: update INSERT policy for super_admin
drop policy if exists "property_prices: authorized can insert/update" on public.property_prices;
create policy "property_prices: authorized can insert"
  on public.property_prices for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.update'))
  );

-- project_images
drop policy if exists "project_images: authorized insert" on public.project_images;
create policy "project_images: authorized insert"
  on public.project_images for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('project.update'))
  );

-- property_images
drop policy if exists "property_images: authorized insert" on public.property_images;
create policy "property_images: authorized insert"
  on public.property_images for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.update'))
  );

-- property_documents
drop policy if exists "property_docs: authorized insert" on public.property_documents;
create policy "property_docs: authorized insert"
  on public.property_documents for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.update'))
  );

-- System triggers / super_admin insert policies for history tables
drop policy if exists "prop_status_hist: system can insert" on public.property_status_history;
create policy "prop_status_hist: insert"
  on public.property_status_history for insert
  to authenticated
  with check (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

drop policy if exists "price_history: system can insert" on public.property_price_history;
create policy "price_history: insert"
  on public.property_price_history for insert
  to authenticated
  with check (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

drop policy if exists "appt_history: system can insert" on public.appointment_history;
create policy "appt_history: insert"
  on public.appointment_history for insert
  to authenticated
  with check (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

drop policy if exists "activities: system can insert" on public.activities;
create policy "activities: insert"
  on public.activities for insert
  to authenticated
  with check (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

-- ---------------------------------------------------------------------------
-- 8. SECURE PUBLIC WEBSITE VIEWS
-- ---------------------------------------------------------------------------

create or replace view public.v_public_projects as
select
  p.id as project_id,
  p.tenant_id,
  p.name,
  p.slug,
  p.description,
  p.developer_name,
  p.rera_number,
  p.rera_state,
  p.rera_url,
  p.locality,
  p.city,
  p.state,
  p.country,
  p.pincode,
  p.latitude,
  p.longitude,
  p.status,
  p.price_min,
  p.price_max,
  p.currency,
  p.total_units,
  p.available_units,
  p.project_area,
  p.project_area_unit,
  p.metadata,
  p.created_at
from public.projects p
where p.is_public = true
  and p.status in ('launched', 'under_construction', 'ready_to_move', 'completed')
  and p.deleted_at is null;

comment on view public.v_public_projects is
  'Public marketing view of projects for Next.js frontend. Excludes internal fields.';

create or replace view public.v_public_properties as
select
  pr.id as property_id,
  pr.tenant_id,
  pr.project_id,
  pt.name as property_type_name,
  pt.code as property_type_code,
  pr.property_code,
  pr.unit_number,
  pr.block,
  pr.floor_number,
  pr.plot_area,
  pr.built_up_area,
  pr.carpet_area,
  pr.area_unit,
  pr.bedrooms,
  pr.bathrooms,
  pr.balconies,
  pr.facing,
  pr.base_price,
  pr.offer_price,
  pr.price_per_unit,
  pr.currency,
  pr.status,
  pr.custom_attributes
from public.properties pr
join public.property_types pt on pt.id = pr.property_type_id
where pr.is_public = true
  and pr.status = 'available'
  and pr.deleted_at is null;

comment on view public.v_public_properties is
  'Public inventory view of available properties for Next.js website. Excludes internal CRM notes.';

-- ---------------------------------------------------------------------------
-- 9. DATABASE GRANTS, REVOKES, AND DEFAULT PRIVILEGES
-- ---------------------------------------------------------------------------

-- Revoke all public access
revoke all on all tables in schema public from public, anon;
revoke all on all sequences in schema public from public, anon;
revoke all on all functions in schema public from public, anon;

-- Grant permissions to service_role (FastAPI backend)
grant select, insert, update, delete on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;
grant execute on all functions in schema public to service_role;

-- Grant permissions to authenticated users (RLS enforces boundary)
grant select, insert, update, delete on all tables in schema public to authenticated;
grant usage, select on all sequences in schema public to authenticated;

-- Explicitly grant EXECUTE only on allowed RPC / helper functions to authenticated
grant execute on function public.get_current_tenant_id() to authenticated, service_role;
grant execute on function public.is_super_admin() to authenticated, service_role;
grant execute on function public.is_service_role() to authenticated, service_role, postgres;
grant execute on function public.has_permission(text) to authenticated, service_role;
grant execute on function public.book_site_visit to authenticated, service_role;
grant execute on function public.reserve_property to authenticated, service_role;

-- Grant SELECT on public views to anon & authenticated
grant select on public.v_public_projects to anon, authenticated, service_role;
grant select on public.v_public_properties to anon, authenticated, service_role;

-- Configure future default privileges
alter default privileges in schema public revoke all on tables from public, anon;
alter default privileges in schema public revoke all on sequences from public, anon;
alter default privileges in schema public revoke all on functions from public, anon;

-- ---------------------------------------------------------------------------
-- 10. EXTRA DATA INTEGRITY CHECK CONSTRAINTS
-- ---------------------------------------------------------------------------

alter table public.projects
  drop constraint if exists chk_projects_price,
  drop constraint if exists chk_projects_units;

alter table public.projects
  add constraint chk_projects_price check (price_min is null or price_max is null or price_max >= price_min),
  add constraint chk_projects_units check ((total_units is null or total_units >= 0) and (available_units is null or available_units >= 0));

alter table public.tenants
  drop constraint if exists chk_tenants_limits;

alter table public.tenants
  add constraint chk_tenants_limits check (max_users > 0 and max_properties > 0 and max_ai_minutes > 0);

alter table public.users
  drop constraint if exists chk_users_email;

alter table public.users
  add constraint chk_users_email check (length(trim(email)) > 0);

alter table public.customers
  drop constraint if exists chk_customers_phone;

alter table public.customers
  add constraint chk_customers_phone check (length(trim(phone)) > 0);
