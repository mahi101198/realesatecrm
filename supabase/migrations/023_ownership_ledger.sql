-- =============================================================================
-- MIGRATION 023: Ownership Ledger
--
-- Adds the booking -> sale -> ownership pipeline discussed with the business
-- owner: a property can be token-booked, then sold, and the resulting
-- ownership is tracked as an append-style ledger that supports resale chains,
-- optional co-owners, and per-transaction documents.
--
-- New tables:
--   property_bookings             -- token/reservation step, prior to a sale
--   property_sales                -- the completed transaction (money details)
--   property_ownerships           -- who owns what, right now and historically
--   property_ownership_co_owners  -- optional additional owners (empty by default)
--
-- Existing table touched (additive only):
--   property_documents  -- + nullable ownership_id column, so sale-specific
--                           documents (KYC, sale deed) attach to a specific
--                           ownership record instead of only the property.
--
-- Explicitly OUT OF SCOPE for this migration (per business decision):
--   - No locations table (city/locality stay free text on projects for now).
--   - No phase concept.
--   - No payment/installment tracking (booking_amount/sale_amount are single
--     figures, not schedules).
--   - No app-layer (repository/service/router) changes.
--
-- Design notes:
--   - booking / sale / ownership are three separate tables (not one), because
--     they answer three different questions: "is there a reservation on this
--     unit", "what was actually paid", and "who owns it now / who owned it
--     before". Keeping them separate means a booking can be cancelled without
--     touching sales or ownership history, and a sale can be corrected
--     without rewriting the ownership chain.
--   - Every status is soft (booking_status / sale_status / ownership_status
--     enums); none of these tables have a delete RLS policy for normal users
--     -- corrections happen by changing status, not by deleting rows, same
--     philosophy as property_status_history / property_price_history.
--   - "primary sale" vs "resale" is not a separate column: it is implied by
--     property_ownerships.previous_ownership_id being NULL (primary sale, no
--     prior owner in this system) vs NOT NULL (resale, chained to the prior
--     owner's row).
--   - purchase_purpose reuses the existing public.purpose enum (already has
--     investment / end_use / other, plus rental / resale / commercial_use)
--     rather than inventing a narrower duplicate type.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. NEW ENUMS
-- ---------------------------------------------------------------------------

do $$ begin
  if not exists (select 1 from pg_type where typname = 'booking_status') then
    create type public.booking_status as enum (
      'active',      -- reservation is live, holding the unit
      'cancelled',    -- customer/company backed out, unit freed up
      'converted'    -- this booking became a property_sales row
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'sale_status') then
    create type public.sale_status as enum (
      'active',      -- normal, in-force transaction
      'cancelled',    -- deal fell through before it ever took effect
      'reversed'     -- deal was undone after taking effect (refund/dispute)
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'ownership_status') then
    create type public.ownership_status as enum (
      'active',      -- normal ownership record
      'reversed'     -- entered in error or later invalidated; kept for audit
    );
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 2. PROPERTY_BOOKINGS
-- ---------------------------------------------------------------------------
-- Initial reservation/token-booking step, before a full sale closes.
-- Not every sale has a prior booking (property_sales.booking_id is nullable).
-- ---------------------------------------------------------------------------

create table public.property_bookings (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  property_id          uuid not null,
  customer_id          uuid not null,
  booking_date         date not null default current_date,
  booking_amount       numeric(15, 2) not null,
  booking_status       public.booking_status not null default 'active',
  notes                text,
  created_by           uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),

  constraint uq_property_bookings_tenant_id unique (tenant_id, id),
  constraint chk_property_bookings_amount check (booking_amount >= 0),

  constraint fk_property_bookings_property_tenant
    foreign key (tenant_id, property_id) references public.properties (tenant_id, id) on delete restrict,
  constraint fk_property_bookings_customer_tenant
    foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete restrict
);

create index idx_property_bookings_tenant_id on public.property_bookings (tenant_id);
create index idx_property_bookings_property  on public.property_bookings (tenant_id, property_id);
create index idx_property_bookings_customer  on public.property_bookings (tenant_id, customer_id);
create index idx_property_bookings_status    on public.property_bookings (tenant_id, booking_status);

create trigger trg_property_bookings_updated_at
  before update on public.property_bookings
  for each row execute function public.set_updated_at();

comment on table public.property_bookings is
  'Token/reservation booking on a unit, prior to a full sale closing. '
  'booking_status = converted means this booking became a property_sales row; '
  'cancelled means the reservation fell through and the unit is free again.';

-- ---------------------------------------------------------------------------
-- 3. PROPERTY_SALES
-- ---------------------------------------------------------------------------
-- The completed transaction and its money details. Deliberately separate
-- from property_ownerships: this table answers "what was paid", the
-- ownership ledger answers "who owns it now / who owned it before".
-- ---------------------------------------------------------------------------

create table public.property_sales (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  booking_id           uuid,                     -- optional: sale may not have had a prior booking
  property_id          uuid not null,
  customer_id          uuid not null,
  sale_date            date not null default current_date,
  sale_amount          numeric(15, 2) not null,
  discount_amount      numeric(15, 2) not null default 0,
  tax_amount           numeric(15, 2) not null default 0,
  sale_status          public.sale_status not null default 'active',
  created_by           uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),

  constraint uq_property_sales_tenant_id unique (tenant_id, id),
  constraint chk_property_sales_amount    check (sale_amount >= 0),
  constraint chk_property_sales_discount  check (discount_amount >= 0),
  constraint chk_property_sales_tax       check (tax_amount >= 0),

  constraint fk_property_sales_booking_tenant
    foreign key (tenant_id, booking_id) references public.property_bookings (tenant_id, id) on delete set null,
  constraint fk_property_sales_property_tenant
    foreign key (tenant_id, property_id) references public.properties (tenant_id, id) on delete restrict,
  constraint fk_property_sales_customer_tenant
    foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete restrict
);

create index idx_property_sales_tenant_id on public.property_sales (tenant_id);
create index idx_property_sales_property  on public.property_sales (tenant_id, property_id);
create index idx_property_sales_customer  on public.property_sales (tenant_id, customer_id);
create index idx_property_sales_booking   on public.property_sales (tenant_id, booking_id) where booking_id is not null;
create index idx_property_sales_status    on public.property_sales (tenant_id, sale_status);

create trigger trg_property_sales_updated_at
  before update on public.property_sales
  for each row execute function public.set_updated_at();

comment on table public.property_sales is
  'The completed transaction and its money details (sale price, discount, tax). '
  'Cancelled/reversed sales are marked via sale_status, never hard-deleted. '
  'booking_id is nullable: a sale does not require a prior token booking.';

-- ---------------------------------------------------------------------------
-- 4. PROPERTY_OWNERSHIPS (the ownership ledger)
-- ---------------------------------------------------------------------------
-- Append-style ledger: one row per period a customer (primary owner; see
-- property_ownership_co_owners for additional owners) held a given property.
-- previous_ownership_id chains a resale row back to the prior owner's row.
-- ---------------------------------------------------------------------------

create table public.property_ownerships (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  property_id           uuid not null,
  customer_id           uuid not null,           -- primary owner for this period
  sale_id               uuid,                     -- nullable: allows manual/legacy entry without a sale row
  purchase_purpose      public.purpose,           -- reuses existing enum (investment/end_use/other/...)
  previous_ownership_id uuid,                     -- NULL = primary sale; set = resale, chained to prior owner
  ownership_start_date  date not null default current_date,
  ownership_end_date    date,                     -- NULL = current owner
  ownership_status      public.ownership_status not null default 'active',
  created_by            uuid references public.users (id) on delete set null,
  verified_by           uuid references public.users (id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_property_ownerships_tenant_id unique (tenant_id, id),
  constraint chk_property_ownerships_dates
    check (ownership_end_date is null or ownership_end_date >= ownership_start_date),

  constraint fk_property_ownerships_property_tenant
    foreign key (tenant_id, property_id) references public.properties (tenant_id, id) on delete restrict,
  constraint fk_property_ownerships_customer_tenant
    foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete restrict,
  constraint fk_property_ownerships_sale_tenant
    foreign key (tenant_id, sale_id) references public.property_sales (tenant_id, id) on delete set null,
  constraint fk_property_ownerships_previous_tenant
    foreign key (tenant_id, previous_ownership_id) references public.property_ownerships (tenant_id, id) on delete set null
);

create index idx_property_ownerships_tenant_id on public.property_ownerships (tenant_id);
create index idx_property_ownerships_property  on public.property_ownerships (tenant_id, property_id);
create index idx_property_ownerships_customer  on public.property_ownerships (tenant_id, customer_id);
create index idx_property_ownerships_sale       on public.property_ownerships (tenant_id, sale_id) where sale_id is not null;
create index idx_property_ownerships_previous   on public.property_ownerships (previous_ownership_id) where previous_ownership_id is not null;

-- CRITICAL CONSTRAINT: exactly one current owner-of-record per property.
-- This is the DB-level guarantee behind "who owns this property right now"
-- (SELECT ... WHERE property_id = ? AND ownership_end_date IS NULL).
create unique index uq_property_ownerships_one_active_per_property
  on public.property_ownerships (tenant_id, property_id)
  where ownership_end_date is null;

create trigger trg_property_ownerships_updated_at
  before update on public.property_ownerships
  for each row execute function public.set_updated_at();

comment on table public.property_ownerships is
  'Append-style ownership ledger. One row per period a customer (or ownership '
  'group, see property_ownership_co_owners) held a given property. '
  'previous_ownership_id chains a resale row back to the prior owner''s row, '
  'giving the full resale history for a property via property_id. '
  'Reversed/cancelled transactions are marked via ownership_status, never hard-deleted.';

comment on index public.uq_property_ownerships_one_active_per_property is
  'Enforces at most one row per (tenant_id, property_id) with ownership_end_date '
  'IS NULL at any time -- i.e. exactly one current owner-of-record per property.';

comment on column public.property_ownerships.ownership_end_date is
  'NULL means this is the current owner. Set when this ownership period ends '
  '(property resold, or the record reversed).';

comment on column public.property_ownerships.previous_ownership_id is
  'Links a resale row back to the ownership row of the person they bought it from. '
  'NULL on a primary sale (no prior owner of this property in this system).';

comment on column public.property_ownerships.sale_id is
  'Nullable link to the property_sales row that produced this ownership. Left NULL '
  'for manually entered or legacy ownership records that predate this pipeline.';

-- ---------------------------------------------------------------------------
-- 5. PROPERTY_OWNERSHIP_CO_OWNERS
-- ---------------------------------------------------------------------------
-- Optional additional owners beyond the primary owner on property_ownerships.
-- Empty for the common single-owner case; populated only when a sale has
-- joint owners (spouse, family, partners).
-- ---------------------------------------------------------------------------

create table public.property_ownership_co_owners (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references public.tenants (id) on delete restrict,
  ownership_id      uuid not null,
  customer_id       uuid not null,
  role              text,                         -- e.g. 'spouse' / 'partner' / 'family'
  share_percentage  numeric(5, 2),
  created_at        timestamptz not null default now(),

  constraint chk_co_owners_share
    check (share_percentage is null or (share_percentage >= 0 and share_percentage <= 100)),

  constraint uq_co_owners_ownership_customer unique (ownership_id, customer_id),

  constraint fk_co_owners_ownership_tenant
    foreign key (tenant_id, ownership_id) references public.property_ownerships (tenant_id, id) on delete cascade,
  constraint fk_co_owners_customer_tenant
    foreign key (tenant_id, customer_id) references public.customers (tenant_id, id) on delete restrict
);

create index idx_co_owners_ownership on public.property_ownership_co_owners (tenant_id, ownership_id);
create index idx_co_owners_customer  on public.property_ownership_co_owners (tenant_id, customer_id);

comment on table public.property_ownership_co_owners is
  'Optional additional owners beyond the primary owner recorded on '
  'property_ownerships.customer_id. Empty for the common single-owner case, so '
  'adding this table costs nothing for the 90% case while avoiding a future '
  'migration if/when joint ownership needs to be recorded.';

comment on column public.property_ownership_co_owners.share_percentage is
  'Optional ownership share (0-100) for this co-owner. The primary owner''s share '
  'is not stored explicitly; if exact-100% accounting across all owners is needed, '
  'the application layer is responsible for treating the primary owner''s implicit '
  'share as (100 - SUM of co-owner shares). The DB only guarantees the co-owner '
  'SUM itself never exceeds 100 (see trg_validate_co_owner_share below).';

-- ---------------------------------------------------------------------------
-- 6. CONSTRAINT TRIGGER: co-owner share_percentage never sums past 100
-- ---------------------------------------------------------------------------
-- A plain CHECK constraint cannot express a cross-row SUM, so this is done
-- with a deferrable constraint trigger that re-validates the affected
-- ownership_id(s) after every INSERT / UPDATE / DELETE on this table.
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_validate_co_owner_share()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_total numeric(7, 2);
begin
  -- Validate the ownership_id the affected row belongs to after INSERT/UPDATE.
  if tg_op in ('INSERT', 'UPDATE') then
    select coalesce(sum(share_percentage), 0) into v_total
    from   public.property_ownership_co_owners
    where  ownership_id = new.ownership_id;

    if v_total > 100 then
      raise exception
        'property_ownership_co_owners: total share_percentage for ownership_id % would be % (exceeds 100).',
        new.ownership_id, v_total
        using errcode = 'P0004';
    end if;
  end if;

  -- If a row moved to a different ownership_id, or was deleted, also
  -- re-check the ownership_id it left behind (defensive symmetry; sums can
  -- only decrease here, but this keeps the guard consistent both ways).
  if (tg_op = 'UPDATE' and old.ownership_id is distinct from new.ownership_id) or tg_op = 'DELETE' then
    select coalesce(sum(share_percentage), 0) into v_total
    from   public.property_ownership_co_owners
    where  ownership_id = old.ownership_id;

    if v_total > 100 then
      raise exception
        'property_ownership_co_owners: total share_percentage for ownership_id % would be % (exceeds 100).',
        old.ownership_id, v_total
        using errcode = 'P0004';
    end if;
  end if;

  return null; -- return value ignored for AFTER triggers
end;
$$;

comment on function public.trg_fn_validate_co_owner_share() is
  'Constraint-trigger guard: after any INSERT/UPDATE/DELETE on '
  'property_ownership_co_owners, re-validates that SUM(share_percentage) for the '
  'affected ownership_id never exceeds 100. Does not require the sum to reach '
  'exactly 100 -- see property_ownership_co_owners.share_percentage comment.';

create constraint trigger trg_validate_co_owner_share
  after insert or update or delete on public.property_ownership_co_owners
  deferrable initially immediate
  for each row execute function public.trg_fn_validate_co_owner_share();

-- ---------------------------------------------------------------------------
-- 7. PROPERTY_DOCUMENTS: additive column so sale-specific documents attach
--    to a specific ownership record, not only to the property/project.
-- ---------------------------------------------------------------------------

alter table public.property_documents
  add column ownership_id uuid;

alter table public.property_documents
  add constraint fk_property_documents_ownership_tenant
  foreign key (tenant_id, ownership_id) references public.property_ownerships (tenant_id, id) on delete set null;

create index idx_property_docs_ownership on public.property_documents (ownership_id) where ownership_id is not null;

comment on column public.property_documents.ownership_id is
  'Optional link to the specific ownership/transaction record (property_ownerships) '
  'this document belongs to -- e.g. a sale deed or KYC document for one particular '
  'owner in a property''s resale chain. NULL keeps the existing behaviour: a '
  'property- or project-level document (brochure, floor plan, RERA filing) not '
  'tied to any single owner.';

-- ---------------------------------------------------------------------------
-- 8. PERMISSIONS
-- ---------------------------------------------------------------------------

insert into public.permissions (code, name, description, resource, action) values
  ('property_booking.read',     'Read Property Bookings',      'View token/reservation bookings.',          'property_booking',   'read'),
  ('property_booking.create',   'Create Property Bookings',    'Record a new token/reservation booking.',   'property_booking',   'create'),
  ('property_booking.update',   'Update Property Bookings',    'Edit, cancel, or convert a booking.',       'property_booking',   'update'),
  ('property_sale.read',        'Read Property Sales',         'View completed sale transactions.',         'property_sale',      'read'),
  ('property_sale.create',      'Create Property Sales',       'Record a new completed sale.',              'property_sale',      'create'),
  ('property_sale.update',      'Update Property Sales',       'Cancel or reverse a sale.',                 'property_sale',      'update'),
  ('property_ownership.read',   'Read Property Ownerships',    'View current and historical ownership.',    'property_ownership', 'read'),
  ('property_ownership.create', 'Create Property Ownerships',  'Record a new ownership transfer.',          'property_ownership', 'create'),
  ('property_ownership.update', 'Update Property Ownerships',  'Close out, verify, or reverse an ownership record.', 'property_ownership', 'update')
on conflict (code) do nothing;

-- Grants follow the existing per-role pattern (see migration 003's DO block
-- and migration 018's platform.* grants): new permissions are NOT retroactively
-- picked up by roles whose earlier grant was a point-in-time SELECT, so each
-- relevant role is granted explicitly here.

-- admin: full access across booking/sale/ownership (tenant administrator).
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'admin' and r.is_system_role = true
  and  p.code in (
    'property_booking.read', 'property_booking.create', 'property_booking.update',
    'property_sale.read', 'property_sale.create', 'property_sale.update',
    'property_ownership.read', 'property_ownership.create', 'property_ownership.update'
  )
on conflict do nothing;

-- sales_manager: runs the deal desk end-to-end (bookings, sales, resale/ownership transfers).
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'sales_manager' and r.is_system_role = true
  and  p.code in (
    'property_booking.read', 'property_booking.create', 'property_booking.update',
    'property_sale.read', 'property_sale.create', 'property_sale.update',
    'property_ownership.read', 'property_ownership.create', 'property_ownership.update'
  )
on conflict do nothing;

-- sales_agent: can take/manage token bookings; read-only on sales and ownership
-- (finalizing a sale or ownership transfer is a manager/back-office action).
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'sales_agent' and r.is_system_role = true
  and  p.code in (
    'property_booking.read', 'property_booking.create', 'property_booking.update',
    'property_sale.read',
    'property_ownership.read'
  )
on conflict do nothing;

-- manager: reporting-level read access, matching its existing "reporting access" role.
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'manager' and r.is_system_role = true
  and  p.code in (
    'property_booking.read', 'property_sale.read', 'property_ownership.read'
  )
on conflict do nothing;

-- viewer: read-only, matching its existing "all *.read" intent (the original
-- grant was `where code like '%.read'` at migration-003 time, which does not
-- retroactively include these new codes -- so it is re-applied explicitly here).
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'viewer' and r.is_system_role = true
  and  p.code in (
    'property_booking.read', 'property_sale.read', 'property_ownership.read'
  )
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- 9. ROW LEVEL SECURITY
-- ---------------------------------------------------------------------------
-- Pattern matches migrations 010/018/019: service_role bypass, then
-- is_super_admin() OR (tenant match AND permission) for authenticated users.
-- No DELETE policy on any of these tables for authenticated users -- these
-- are audit-relevant records; corrections happen via status columns, not
-- row deletion (same philosophy as property_status_history).
-- ---------------------------------------------------------------------------

-- ---- PROPERTY_BOOKINGS ----
alter table public.property_bookings enable row level security;

create policy "property_bookings: service_role full access"
  on public.property_bookings for all
  to service_role using (true) with check (true);

create policy "property_bookings: read"
  on public.property_bookings for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_booking.read'))
  );

create policy "property_bookings: authorized can insert"
  on public.property_bookings for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_booking.create'))
  );

create policy "property_bookings: authorized can update"
  on public.property_bookings for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_booking.update'))
  );

-- ---- PROPERTY_SALES ----
alter table public.property_sales enable row level security;

create policy "property_sales: service_role full access"
  on public.property_sales for all
  to service_role using (true) with check (true);

create policy "property_sales: read"
  on public.property_sales for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_sale.read'))
  );

create policy "property_sales: authorized can insert"
  on public.property_sales for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_sale.create'))
  );

create policy "property_sales: authorized can update"
  on public.property_sales for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_sale.update'))
  );

-- ---- PROPERTY_OWNERSHIPS ----
alter table public.property_ownerships enable row level security;

create policy "property_ownerships: service_role full access"
  on public.property_ownerships for all
  to service_role using (true) with check (true);

create policy "property_ownerships: read"
  on public.property_ownerships for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_ownership.read'))
  );

create policy "property_ownerships: authorized can insert"
  on public.property_ownerships for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_ownership.create'))
  );

create policy "property_ownerships: authorized can update"
  on public.property_ownerships for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_ownership.update'))
  );

-- ---- PROPERTY_OWNERSHIP_CO_OWNERS ----
-- Simple join-style table: reuses the property_ownership.* permission codes
-- rather than defining its own (same pattern as lead_property_interests
-- reusing lead.read/lead.update in migration 018).
alter table public.property_ownership_co_owners enable row level security;

create policy "property_ownership_co_owners: service_role full access"
  on public.property_ownership_co_owners for all
  to service_role using (true) with check (true);

create policy "property_ownership_co_owners: access"
  on public.property_ownership_co_owners for all
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_ownership.read'))
  )
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_ownership.update'))
  );
