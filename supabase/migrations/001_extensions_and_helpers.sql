-- =============================================================================
-- MIGRATION 001: Extensions, Helper Functions, and Shared Triggers
-- =============================================================================
-- This migration sets up the PostgreSQL extensions, reusable trigger functions,
-- and tenant-resolution helpers required by every subsequent migration.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. EXTENSIONS
-- ---------------------------------------------------------------------------

create extension if not exists "uuid-ossp";       -- UUID generation (legacy compat)
create extension if not exists "pgcrypto";         -- gen_random_uuid(), encryption helpers
create extension if not exists "pg_trgm";          -- trigram indexes for fast text search
create extension if not exists "btree_gin";        -- composite GIN indexes
create extension if not exists "unaccent";         -- accent-insensitive search support

-- ---------------------------------------------------------------------------
-- 2. UPDATED_AT TRIGGER FUNCTION
--    Reused by every table that carries an updated_at column.
-- ---------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

comment on function public.set_updated_at() is
  'Automatically sets updated_at to now() on every UPDATE.';

-- ---------------------------------------------------------------------------
-- 3. TENANT RESOLUTION FUNCTION
--    Returns the tenant_id associated with the currently authenticated
--    Supabase user. Used by all RLS policies.
--
--    SECURITY: The function is SECURITY DEFINER so it can read the users
--    table without triggering its own RLS. It is intentionally simple so
--    that FastAPI service-role connections bypass it entirely.
-- ---------------------------------------------------------------------------

create or replace function public.get_current_tenant_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select tenant_id
  from   public.users
  where  auth_user_id = auth.uid()
  limit  1;
$$;

comment on function public.get_current_tenant_id() is
  'Returns the tenant_id of the currently authenticated Supabase user. '
  'Used in RLS policies across all tenant-owned tables.';

-- ---------------------------------------------------------------------------
-- 4. HELPER: IS_SERVICE_ROLE
--    Returns TRUE when the calling role is service_role (FastAPI back-end).
--    Lets certain RLS policies grant full access to the trusted API layer.
-- ---------------------------------------------------------------------------

create or replace function public.is_service_role()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select current_setting('role', true) = 'service_role'
      or (select rolname from pg_roles where oid = current_user::regrole) = 'service_role';
$$;

comment on function public.is_service_role() is
  'Returns TRUE when called from the Supabase service role (FastAPI). '
  'Allows trusted back-end operations to bypass tenant RLS filters.';

-- ---------------------------------------------------------------------------
-- 5. MACRO: ATTACH UPDATED_AT TRIGGER
--    Called once per table to wire up the set_updated_at() function.
-- ---------------------------------------------------------------------------

create or replace function public.attach_updated_at_trigger(p_table text)
returns void
language plpgsql
as $$
begin
  execute format(
    'create trigger trg_%I_updated_at
       before update on public.%I
       for each row execute function public.set_updated_at()',
    p_table, p_table
  );
end;
$$;

comment on function public.attach_updated_at_trigger(text) is
  'Utility to programmatically attach the updated_at trigger to a table.';
