-- =============================================================================
-- MIGRATION 025: Property Lifecycle Expansion
--
-- Builds out four gaps flagged (and deliberately deferred) during earlier
-- schema discussion, now brought into scope by the business owner:
--
--   1. property_type_history       -- plot -> villa (etc.) reclassification,
--                                      mirroring property_status_history exactly.
--   2. properties.construction_status (fast summary) +
--      property_construction_milestones (granular, mutable detail).
--   3. property_sale_payments      -- installment ledger against a sale,
--                                      + v_property_sale_balances convenience view.
--   4. property_resale_listings    -- CRM-adjacent "owner wants to resell"
--                                      intent, structurally separate from the
--                                      property_ownerships transaction ledger.
--
-- The target database has zero rows of real data, so no backfill concerns
-- apply here -- but the structural conventions (tenant-safe composite FKs,
-- RLS, permission-seeding, no-hard-delete) are unchanged from 023/024.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. NEW ENUMS
-- ---------------------------------------------------------------------------

do $$ begin
  if not exists (select 1 from pg_type where typname = 'property_type_change_reason') then
    create type public.property_type_change_reason as enum (
      'construction_completed',  -- plot -> villa/house after building is done
      'reclassification',        -- e.g. shop -> office, corrected category
      'correction',               -- data-entry fix, not a real-world change
      'other'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'construction_status') then
    create type public.construction_status as enum (
      'not_applicable', -- e.g. a plot with no construction planned
      'not_started',
      'in_progress',
      'on_hold',
      'completed'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'construction_milestone') then
    create type public.construction_milestone as enum (
      'foundation',
      'structure',                  -- RCC / framing
      'brickwork_and_plastering',
      'electrical_and_plumbing',
      'finishing',                  -- flooring, painting, fixtures
      'handover'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'construction_milestone_status') then
    create type public.construction_milestone_status as enum (
      'pending',
      'in_progress',
      'completed'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'payment_mode') then
    create type public.payment_mode as enum (
      'cash',
      'cheque',
      'bank_transfer',
      'upi',
      'card',
      'other'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'payment_status') then
    create type public.payment_status as enum (
      'pending',
      'received',
      'bounced',
      'refunded'
    );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_type where typname = 'resale_listing_status') then
    create type public.resale_listing_status as enum (
      'open',       -- currently listed, looking for a buyer
      'withdrawn',  -- owner changed their mind, no sale happened
      'converted'   -- resulted in an actual resale (new property_sales + property_ownerships row)
    );
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 2. PROPERTY_TYPE_HISTORY
-- ---------------------------------------------------------------------------
-- Mirrors property_status_history (004_property_project.sql) and its
-- actor-capture trigger rewrite (014_integrity_corrections.sql) exactly:
-- append-only, auto-populated by a trigger on properties, actor read from
-- the app-set session variable rather than trusted client input.
-- ---------------------------------------------------------------------------

create table public.property_type_history (
  id                          uuid primary key default gen_random_uuid(),
  tenant_id                   uuid not null references public.tenants (id) on delete restrict,
  property_id                 uuid not null,
  previous_property_type_id   uuid references public.property_types (id) on delete restrict,
  new_property_type_id        uuid not null references public.property_types (id) on delete restrict,
  reason                      public.property_type_change_reason,
  changed_by_user_id          uuid references public.users (id) on delete set null,
  changed_by_type             text not null default 'system',   -- 'user' | 'system' | 'ai_agent' -- mirrors property_status_history
  metadata                    jsonb not null default '{}',
  created_at                  timestamptz not null default now(),

  constraint fk_property_type_history_property_tenant
    foreign key (tenant_id, property_id) references public.properties (tenant_id, id) on delete restrict
);

create index idx_property_type_hist_property on public.property_type_history (property_id, created_at desc);
create index idx_property_type_hist_tenant   on public.property_type_history (tenant_id, created_at desc);

comment on table public.property_type_history is
  'Append-only log of property_type_id reclassifications (e.g. plot -> villa '
  'after construction completes). Auto-populated by trg_properties_type_change; '
  'never written to directly by the app. Immutable, same philosophy as '
  'property_status_history.';

comment on column public.property_type_history.reason is
  'Unlike property_status_history.reason (free text), this is a small fixed '
  'enum: type reclassifications happen for a narrow, well-understood set of '
  'reasons, so a queryable enum ("show all changes due to construction '
  'completion this quarter") is more useful here than free text. The '
  'auto-insert trigger itself cannot know the reason -- it is left NULL at '
  'insert time and may be filled in by a follow-up UPDATE from the app layer, '
  'the same pattern reserve_property() uses to annotate property_status_history.';

create or replace function public.trg_fn_property_type_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor_user_id  uuid;
  v_actor_type     text;
begin
  if old.property_type_id is distinct from new.property_type_id then
    -- Try to read actor from session variable set by FastAPI (same idiom as
    -- trg_fn_property_status_change in migration 014).
    begin
      v_actor_user_id := nullif(current_setting('app.current_user_id', true), '')::uuid;
    exception when others then
      v_actor_user_id := null;
    end;

    v_actor_type := case
      when v_actor_user_id is not null then 'user'
      else 'system'
    end;

    insert into public.property_type_history (
      tenant_id, property_id,
      previous_property_type_id, new_property_type_id,
      changed_by_user_id, changed_by_type
    ) values (
      new.tenant_id, new.id,
      old.property_type_id, new.property_type_id,
      v_actor_user_id, v_actor_type
    );
  end if;
  return new;
end;
$$;

comment on function public.trg_fn_property_type_change() is
  'Records property_type_id reclassifications in property_type_history. '
  'Reads actor from SET LOCAL app.current_user_id if set by FastAPI, exactly '
  'mirroring trg_fn_property_status_change.';

create trigger trg_properties_type_change
  after update on public.properties
  for each row execute function public.trg_fn_property_type_change();

-- ---------------------------------------------------------------------------
-- 3a. PROPERTIES: construction_status (fast-read summary column)
-- ---------------------------------------------------------------------------

alter table public.properties
  add column construction_status public.construction_status;

-- Full comment (referencing property_construction_milestones) is added below,
-- once that table exists.

-- ---------------------------------------------------------------------------
-- 3b. PROPERTY_CONSTRUCTION_MILESTONES
-- ---------------------------------------------------------------------------
-- One row per (property, milestone): a mutable "current state of this stage"
-- table (has updated_at, gets progressed pending -> in_progress -> completed
-- in place), NOT an append-only ledger like property_status_history. This is
-- the granular detail behind properties.construction_status.
-- ---------------------------------------------------------------------------

create table public.property_construction_milestones (
  id                       uuid primary key default gen_random_uuid(),
  tenant_id                uuid not null references public.tenants (id) on delete restrict,
  property_id              uuid not null,
  milestone                public.construction_milestone not null,
  status                   public.construction_milestone_status not null default 'pending',
  target_date              date,
  actual_completion_date   date,
  verified_by              uuid references public.users (id) on delete set null,
  notes                    text,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),

  constraint uq_property_construction_milestones_property_stage
    unique (property_id, milestone),

  constraint fk_property_construction_milestones_property_tenant
    foreign key (tenant_id, property_id) references public.properties (tenant_id, id) on delete restrict
);

create index idx_property_construction_milestones_tenant   on public.property_construction_milestones (tenant_id);
create index idx_property_construction_milestones_property on public.property_construction_milestones (tenant_id, property_id);
create index idx_property_construction_milestones_status   on public.property_construction_milestones (tenant_id, status);

create trigger trg_property_construction_milestones_updated_at
  before update on public.property_construction_milestones
  for each row execute function public.set_updated_at();

comment on table public.property_construction_milestones is
  'Granular, per-stage construction tracking for a property. One row per '
  '(property_id, milestone) -- updated in place as the stage progresses, not '
  'appended. The milestone set (foundation / structure / brickwork_and_plastering '
  '/ electrical_and_plumbing / finishing / handover) is a judgment-call fixed '
  'set representative of standard residential construction phases; easy to '
  'extend via a future enum value if the business uses a different breakdown.';

comment on column public.properties.construction_status is
  'Fast-read summary for filtering/listing (e.g. "all plots where construction '
  'is in progress"). Nullable and no default, because it does not apply to '
  'every property at every point in time (e.g. a plot with no construction '
  'planned should be set to not_applicable explicitly, not left to a guessed '
  'default). See also property_construction_milestones for granular per-stage '
  'detail. Kept independently settable rather than trigger-derived from the '
  'milestones table: rolling many milestone statuses into one summary value '
  'requires a business rule (e.g. "is construction complete only when '
  'handover is marked completed, or when all stages are?") that is not a '
  'purely structural fact the schema should hard-code. If real usage settles '
  'on one clear rule, a future migration can add a sync trigger.';

-- ---------------------------------------------------------------------------
-- 4. PROPERTY_SALE_PAYMENTS
-- ---------------------------------------------------------------------------
-- Installment ledger against a property_sales row. No payment_mode/status
-- enum existed anywhere in this schema prior to this migration -- both are
-- new, introduced here.
-- ---------------------------------------------------------------------------

create table public.property_sale_payments (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references public.tenants (id) on delete restrict,
  sale_id            uuid not null,
  payment_date       date not null default current_date,
  amount             numeric(15, 2) not null,
  payment_mode       public.payment_mode not null,
  payment_status     public.payment_status not null default 'pending',
  reference_number   text,                     -- cheque number / UTR / transaction ref
  notes              text,
  created_by         uuid references public.users (id) on delete set null,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),

  constraint chk_property_sale_payments_amount check (amount >= 0),

  constraint fk_property_sale_payments_sale_tenant
    foreign key (tenant_id, sale_id) references public.property_sales (tenant_id, id) on delete restrict
);

create index idx_property_sale_payments_tenant on public.property_sale_payments (tenant_id);
create index idx_property_sale_payments_sale   on public.property_sale_payments (tenant_id, sale_id);
create index idx_property_sale_payments_status on public.property_sale_payments (tenant_id, payment_status);

create trigger trg_property_sale_payments_updated_at
  before update on public.property_sale_payments
  for each row execute function public.set_updated_at();

comment on table public.property_sale_payments is
  'Raw payment ledger against a sale. Supports installments: a sale can have '
  'many payment rows. Outstanding balance = sale_amount - discount_amount - '
  'SUM(amount) WHERE payment_status = ''received'' -- see '
  'v_property_sale_balances for a ready-made rollup of that computation. '
  'Corrections happen via payment_status (pending/bounced/refunded), not by '
  'deleting rows.';

-- Convenience view: trivial aggregation directly useful for a payments
-- dashboard / "who owes what" list. Follows the plain-view pattern already
-- used for internal analytics views in migration 011 (no security_invoker /
-- explicit grants there either) -- access is still gated by the RLS policies
-- on the underlying property_sales / property_sale_payments tables.
create or replace view public.v_property_sale_balances as
select
  ps.id                                                              as sale_id,
  ps.tenant_id,
  ps.property_id,
  ps.customer_id,
  ps.sale_amount,
  ps.discount_amount,
  ps.tax_amount,
  coalesce(sum(psp.amount) filter (where psp.payment_status = 'received'), 0) as amount_received,
  ps.sale_amount - ps.discount_amount
    - coalesce(sum(psp.amount) filter (where psp.payment_status = 'received'), 0) as outstanding_balance
from   public.property_sales ps
left join public.property_sale_payments psp on psp.sale_id = ps.id
group by ps.id, ps.tenant_id, ps.property_id, ps.customer_id,
         ps.sale_amount, ps.discount_amount, ps.tax_amount;

comment on view public.v_property_sale_balances is
  'outstanding_balance = sale_amount - discount_amount - SUM(received payments). '
  'tax_amount is surfaced but deliberately not netted into this formula (its '
  'treatment relative to the balance was not specified and is left to the '
  'application/reporting layer to interpret).';

-- ---------------------------------------------------------------------------
-- 5. PROPERTY_RESALE_LISTINGS
-- ---------------------------------------------------------------------------
-- The CRM-adjacent front half of a resale: "the current owner wants to
-- sell", before it becomes an actual transaction. Structurally separate from
-- property_ownerships -- a listing does not touch the ownership ledger at
-- all until/unless it converts into a real resale (new property_sales +
-- property_ownerships row, at which point listing_status becomes converted).
-- ---------------------------------------------------------------------------

create table public.property_resale_listings (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references public.tenants (id) on delete restrict,
  ownership_id       uuid not null,           -- intended: the CURRENT owner's row (ownership_end_date IS NULL) -- business rule, not enforced structurally
  listed_at          timestamptz not null default now(),
  asking_price       numeric(15, 2),
  listing_status     public.resale_listing_status not null default 'open',
  notes              text,
  created_by         uuid references public.users (id) on delete set null,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),

  constraint chk_property_resale_listings_asking_price check (asking_price is null or asking_price >= 0),

  constraint fk_property_resale_listings_ownership_tenant
    foreign key (tenant_id, ownership_id) references public.property_ownerships (tenant_id, id) on delete restrict
);

create index idx_property_resale_listings_tenant    on public.property_resale_listings (tenant_id);
create index idx_property_resale_listings_ownership on public.property_resale_listings (tenant_id, ownership_id);
create index idx_property_resale_listings_open      on public.property_resale_listings (tenant_id, listing_status) where listing_status = 'open';

-- Not requested explicitly, but a direct extension of the "one active X"
-- pattern already used for property_ownerships (023) and locations (024):
-- prevents accidentally creating two simultaneous open listings for the same
-- ownership row.
create unique index uq_property_resale_listings_one_open_per_ownership
  on public.property_resale_listings (ownership_id)
  where listing_status = 'open';

create trigger trg_property_resale_listings_updated_at
  before update on public.property_resale_listings
  for each row execute function public.set_updated_at();

comment on table public.property_resale_listings is
  'The pre-transaction "owner wants to resell" intent, deliberately separate '
  'from property_ownerships. ownership_id should point at the current '
  'owner''s row (ownership_end_date IS NULL) -- this is a business-logic '
  'expectation, not structurally enforced by a constraint, since "current" is '
  'a time-varying condition on the parent row, not a static fact this FK can '
  'check. listing_status = converted is set once the resale actually closes '
  '(a new property_sales row and a new property_ownerships row are created, '
  'chained via previous_ownership_id back to this listing''s ownership_id).';

comment on index public.uq_property_resale_listings_one_open_per_ownership is
  'At most one open resale listing per ownership record at a time.';

-- ---------------------------------------------------------------------------
-- 6. PERMISSIONS
-- ---------------------------------------------------------------------------
-- property_type_history: no dedicated permission. It is system/trigger
-- populated and read via the same permission that already gates seeing
-- property detail -- exactly how property_status_history reuses
-- 'property.read' rather than having its own code (see migration 010).
-- ---------------------------------------------------------------------------

insert into public.permissions (code, name, description, resource, action) values
  ('construction_milestone.read',    'Read Construction Milestones',   'View per-stage construction tracking.',        'construction_milestone', 'read'),
  ('construction_milestone.create',  'Create Construction Milestones', 'Add a construction milestone record.',         'construction_milestone', 'create'),
  ('construction_milestone.update',  'Update Construction Milestones', 'Progress or verify a construction milestone.', 'construction_milestone', 'update'),
  ('property_sale_payment.read',     'Read Sale Payments',             'View payment/installment ledger for a sale.',  'property_sale_payment',  'read'),
  ('property_sale_payment.create',   'Create Sale Payments',           'Record a new payment against a sale.',         'property_sale_payment',  'create'),
  ('property_sale_payment.update',   'Update Sale Payments',           'Correct a payment''s status (e.g. bounced/refunded).', 'property_sale_payment', 'update'),
  ('resale_listing.read',            'Read Resale Listings',           'View owner resale-intent listings.',           'resale_listing',         'read'),
  ('resale_listing.create',          'Create Resale Listings',         'Record that an owner wants to resell.',        'resale_listing',         'create'),
  ('resale_listing.update',          'Update Resale Listings',         'Withdraw or convert a resale listing.',        'resale_listing',         'update')
on conflict (code) do nothing;

-- admin: full access across all three new resources.
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'admin' and r.is_system_role = true
  and  p.code in (
    'construction_milestone.read', 'construction_milestone.create', 'construction_milestone.update',
    'property_sale_payment.read', 'property_sale_payment.create', 'property_sale_payment.update',
    'resale_listing.read', 'resale_listing.create', 'resale_listing.update'
  )
on conflict do nothing;

-- sales_manager: full access (runs the deal desk, same as booking/sale/ownership in 023).
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'sales_manager' and r.is_system_role = true
  and  p.code in (
    'construction_milestone.read', 'construction_milestone.create', 'construction_milestone.update',
    'property_sale_payment.read', 'property_sale_payment.create', 'property_sale_payment.update',
    'resale_listing.read', 'resale_listing.create', 'resale_listing.update'
  )
on conflict do nothing;

-- sales_agent: read-only on construction and payments (progressing a
-- construction stage or correcting a payment is a back-office/manager
-- action); full read/create/update on resale listings, since fielding "I
-- want to sell" from an existing owner is a front-line sales conversation,
-- the same breadth already given to sales_agent for property_booking in 023.
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'sales_agent' and r.is_system_role = true
  and  p.code in (
    'construction_milestone.read',
    'property_sale_payment.read',
    'resale_listing.read', 'resale_listing.create', 'resale_listing.update'
  )
on conflict do nothing;

-- manager, viewer: read-only across all three, matching the reporting-access
-- distribution already established in 023/024.
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name in ('manager', 'viewer') and r.is_system_role = true
  and  p.code in (
    'construction_milestone.read', 'property_sale_payment.read', 'resale_listing.read'
  )
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- 7. ROW LEVEL SECURITY
-- ---------------------------------------------------------------------------
-- Pattern matches migrations 010/018/019/023/024. No DELETE policy for
-- authenticated users on any table here -- status-driven corrections only.
-- ---------------------------------------------------------------------------

-- ---- PROPERTY_TYPE_HISTORY (append-only, mirrors property_status_history) ----
alter table public.property_type_history enable row level security;

create policy "property_type_history: service_role full access"
  on public.property_type_history for all
  to service_role using (true) with check (true);

create policy "property_type_history: read"
  on public.property_type_history for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property.read'))
  );

-- INSERT allowed for authenticated (system trigger driven, same as
-- property_status_history) -- the actual gate is that changing
-- properties.property_type_id already requires 'property.update' via the
-- properties UPDATE policy; this table only needs a tenant match.
create policy "property_type_history: system can insert"
  on public.property_type_history for insert
  to authenticated
  with check (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  );

-- NO UPDATE / DELETE policy = immutable for normal users.

-- ---- PROPERTY_CONSTRUCTION_MILESTONES ----
alter table public.property_construction_milestones enable row level security;

create policy "construction_milestones: service_role full access"
  on public.property_construction_milestones for all
  to service_role using (true) with check (true);

create policy "construction_milestones: read"
  on public.property_construction_milestones for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('construction_milestone.read'))
  );

create policy "construction_milestones: authorized can insert"
  on public.property_construction_milestones for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('construction_milestone.create'))
  );

create policy "construction_milestones: authorized can update"
  on public.property_construction_milestones for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('construction_milestone.update'))
  );

-- ---- PROPERTY_SALE_PAYMENTS ----
alter table public.property_sale_payments enable row level security;

create policy "property_sale_payments: service_role full access"
  on public.property_sale_payments for all
  to service_role using (true) with check (true);

create policy "property_sale_payments: read"
  on public.property_sale_payments for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_sale_payment.read'))
  );

create policy "property_sale_payments: authorized can insert"
  on public.property_sale_payments for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_sale_payment.create'))
  );

create policy "property_sale_payments: authorized can update"
  on public.property_sale_payments for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('property_sale_payment.update'))
  );

-- ---- PROPERTY_RESALE_LISTINGS ----
alter table public.property_resale_listings enable row level security;

create policy "resale_listings: service_role full access"
  on public.property_resale_listings for all
  to service_role using (true) with check (true);

create policy "resale_listings: read"
  on public.property_resale_listings for select
  to authenticated
  using (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('resale_listing.read'))
  );

create policy "resale_listings: authorized can insert"
  on public.property_resale_listings for insert
  to authenticated
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('resale_listing.create'))
  );

create policy "resale_listings: authorized can update"
  on public.property_resale_listings for update
  to authenticated
  using (
    public.is_super_admin()
    or tenant_id = public.get_current_tenant_id()
  )
  with check (
    public.is_super_admin()
    or (tenant_id = public.get_current_tenant_id() and public.has_permission('resale_listing.update'))
  );
