-- =============================================================================
-- MIGRATION 004: Property & Project Tables
-- property_types → project_types → projects → properties
-- amenities → project_amenities / property_amenities
-- project_images / property_images / property_documents
-- property_prices / property_price_history / property_status_history
-- =============================================================================

-- ---------------------------------------------------------------------------
-- PROPERTY_TYPES
-- ---------------------------------------------------------------------------

create table public.property_types (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid references public.tenants (id) on delete cascade, -- NULL = platform default
  name                text not null,                       -- e.g. "Residential Plot"
  code                text not null,                       -- e.g. "residential_plot"
  description         text,
  is_system_type      boolean not null default false,
  is_active           boolean not null default true,
  sort_order          integer not null default 0,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),

  constraint uq_property_types_tenant_code unique (tenant_id, code)
);

create index idx_property_types_tenant_id on public.property_types (tenant_id);

create trigger trg_property_types_updated_at
  before update on public.property_types
  for each row execute function public.set_updated_at();

-- Seed platform-level property types
insert into public.property_types (name, code, is_system_type, sort_order) values
  ('Residential Plot',   'residential_plot',   true,  1),
  ('Villa',              'villa',              true,  2),
  ('Independent House',  'independent_house',  true,  3),
  ('Apartment',          'apartment',          true,  4),
  ('Commercial Property','commercial_property', true,  5),
  ('Shop',               'shop',               true,  6),
  ('Office',             'office',             true,  7),
  ('Land',               'land',               true,  8),
  ('Farmhouse',          'farmhouse',          true,  9),
  ('Penthouse',          'penthouse',          true, 10),
  ('Studio',             'studio',             true, 11),
  ('Warehouse',          'warehouse',          true, 12),
  ('Other',              'other',              true, 99);

-- ---------------------------------------------------------------------------
-- PROJECT_TYPES
-- ---------------------------------------------------------------------------

create table public.project_types (
  id                  uuid primary key default gen_random_uuid(),
  name                text not null unique,                -- e.g. "Residential Township"
  code                text not null unique,
  is_active           boolean not null default true,
  created_at          timestamptz not null default now()
);

insert into public.project_types (name, code) values
  ('Residential Township',   'residential_township'),
  ('Plotted Development',    'plotted_development'),
  ('Villa Project',          'villa_project'),
  ('Apartment Complex',      'apartment_complex'),
  ('Mixed Use',              'mixed_use'),
  ('Commercial Complex',     'commercial_complex'),
  ('IT Park',                'it_park'),
  ('Farmhouse Project',      'farmhouse_project'),
  ('Industrial',             'industrial'),
  ('Other',                  'other');

-- ---------------------------------------------------------------------------
-- PROJECTS
-- ---------------------------------------------------------------------------

create table public.projects (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  project_type_id      uuid not null references public.project_types (id) on delete restrict,
  name                 text not null,
  slug                 text not null,                      -- tenant-scoped URL slug
  description          text,
  developer_name       text,                               -- Builder/developer name
  rera_number          text,
  rera_state           text,
  rera_url             text,
  -- Address
  address_line1        text,
  address_line2        text,
  locality             text,                               -- Area/locality name
  city                 text not null,
  district             text,
  state                text not null,
  country              text not null default 'India',
  pincode              text,
  latitude             numeric(10, 7),
  longitude            numeric(10, 7),
  -- Dates
  launch_date          date,
  possession_date      date,
  completion_date      date,
  -- Status
  status               public.project_status not null default 'pre_launch',
  is_featured          boolean not null default false,
  is_public            boolean not null default false,     -- Visible on public website
  -- Pricing summary (denormalized convenience fields)
  price_min            numeric(15, 2),
  price_max            numeric(15, 2),
  currency             text not null default 'INR',
  -- Metadata
  total_units          integer,
  available_units      integer,
  project_area         numeric(12, 4),                     -- In acres/hectares
  project_area_unit    public.area_unit default 'acre',
  metadata             jsonb not null default '{}',        -- Brochure links, video URLs, etc.
  created_by           uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  deleted_at           timestamptz,

  constraint uq_projects_tenant_slug unique (tenant_id, slug)
);

create index idx_projects_tenant_id      on public.projects (tenant_id);
create index idx_projects_tenant_status  on public.projects (tenant_id, status);
create index idx_projects_tenant_city    on public.projects (tenant_id, city);
create index idx_projects_tenant_featured on public.projects (tenant_id, is_featured) where is_featured = true;
create index idx_projects_created_at     on public.projects (tenant_id, created_at desc);
-- Trigram index for fuzzy name search
create index idx_projects_name_trgm      on public.projects using gin (name gin_trgm_ops);

create trigger trg_projects_updated_at
  before update on public.projects
  for each row execute function public.set_updated_at();

comment on table public.projects is
  'Top-level real-estate development project. Contains many properties.';

-- ---------------------------------------------------------------------------
-- PROPERTIES
-- ---------------------------------------------------------------------------

create table public.properties (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  project_id           uuid not null references public.projects (id) on delete restrict,
  property_type_id     uuid not null references public.property_types (id) on delete restrict,
  -- Identity
  property_code        text not null,                      -- Unique code within project e.g. "P-101"
  unit_number          text,                               -- e.g. "A-301"
  block                text,                               -- e.g. "Block A"
  floor_number         integer,
  -- Dimensions
  plot_area            numeric(12, 4),
  built_up_area        numeric(12, 4),
  carpet_area          numeric(12, 4),
  super_built_up_area  numeric(12, 4),
  area_unit            public.area_unit not null default 'sqft',
  -- Accommodation
  bedrooms             integer,
  bathrooms            integer,
  balconies            integer,
  parking_covered      integer not null default 0,
  parking_open         integer not null default 0,
  -- Orientation / Features
  facing               public.property_facing,
  is_corner            boolean not null default false,
  is_park_facing       boolean not null default false,
  is_road_facing       boolean not null default false,
  -- Pricing (current snapshot; full history in property_prices)
  base_price           numeric(15, 2),
  offer_price          numeric(15, 2),
  price_per_unit       numeric(12, 2),                     -- Price per sqft / gaj etc.
  currency             text not null default 'INR',
  -- Status
  status               public.property_status not null default 'draft',
  is_public            boolean not null default false,
  is_featured          boolean not null default false,
  -- Custom attributes (non-standard per-property-type fields)
  custom_attributes    jsonb not null default '{}',
  -- Operational
  created_by           uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  deleted_at           timestamptz,

  constraint uq_properties_project_code unique (project_id, property_code),
  constraint chk_properties_base_price      check (base_price is null or base_price >= 0),
  constraint chk_properties_offer_price     check (offer_price is null or offer_price >= 0),
  constraint chk_properties_plot_area       check (plot_area is null or plot_area > 0),
  constraint chk_properties_built_up_area   check (built_up_area is null or built_up_area > 0),
  constraint chk_properties_bedrooms        check (bedrooms is null or bedrooms >= 0),
  constraint chk_properties_parking         check (parking_covered >= 0 and parking_open >= 0)
);

create index idx_properties_tenant_id       on public.properties (tenant_id);
create index idx_properties_project_id      on public.properties (tenant_id, project_id);
create index idx_properties_type            on public.properties (tenant_id, property_type_id);
create index idx_properties_status          on public.properties (tenant_id, status);
create index idx_properties_price           on public.properties (tenant_id, base_price) where base_price is not null;
create index idx_properties_bedrooms        on public.properties (tenant_id, bedrooms) where bedrooms is not null;
create index idx_properties_created_at      on public.properties (tenant_id, created_at desc);
-- Partial index for quick availability queries
create index idx_properties_available       on public.properties (tenant_id, project_id, status)
  where status = 'available';

create trigger trg_properties_updated_at
  before update on public.properties
  for each row execute function public.set_updated_at();

comment on table public.properties is
  'Individual inventory unit within a project. Supports all property types.';

-- ---------------------------------------------------------------------------
-- PROPERTY_STATUS_HISTORY
-- ---------------------------------------------------------------------------
-- Append-only record of every status change on a property.
-- ---------------------------------------------------------------------------

create table public.property_status_history (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  property_id          uuid not null references public.properties (id) on delete restrict,
  previous_status      public.property_status,
  new_status           public.property_status not null,
  reason               text,
  changed_by_user_id   uuid references public.users (id) on delete set null,
  changed_by_type      text not null default 'user',       -- 'user' | 'system' | 'ai_agent'
  related_lead_id      uuid,                               -- FK added after leads table
  related_call_id      uuid,                               -- FK added after calls table
  metadata             jsonb not null default '{}',
  created_at           timestamptz not null default now()
);

create index idx_prop_status_hist_property  on public.property_status_history (property_id, created_at desc);
create index idx_prop_status_hist_tenant    on public.property_status_history (tenant_id, created_at desc);

comment on table public.property_status_history is
  'Append-only log of all property status transitions. Immutable.';

-- ---------------------------------------------------------------------------
-- PROPERTY_PRICES (current effective prices)
-- ---------------------------------------------------------------------------

create table public.property_prices (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  property_id          uuid not null references public.properties (id) on delete restrict,
  price_type           public.price_type not null,
  amount               numeric(15, 2) not null,
  currency             text not null default 'INR',
  effective_from       date not null default current_date,
  effective_until      date,                               -- NULL = currently active
  is_current           boolean not null default true,
  notes                text,
  created_by           uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),

  constraint chk_property_prices_amount    check (amount >= 0),
  constraint chk_property_prices_dates     check (effective_until is null or effective_until >= effective_from)
);

create index idx_property_prices_property  on public.property_prices (property_id, price_type, is_current);
create index idx_property_prices_current   on public.property_prices (property_id, is_current)
  where is_current = true;

create trigger trg_property_prices_updated_at
  before update on public.property_prices
  for each row execute function public.set_updated_at();

comment on table public.property_prices is
  'Current and historical pricing per property per price type.';

-- ---------------------------------------------------------------------------
-- PROPERTY_PRICE_HISTORY
-- ---------------------------------------------------------------------------
-- Append-only snapshot whenever a price is updated or expired.
-- ---------------------------------------------------------------------------

create table public.property_price_history (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  property_id          uuid not null references public.properties (id) on delete restrict,
  property_price_id    uuid references public.property_prices (id) on delete set null,
  price_type           public.price_type not null,
  old_amount           numeric(15, 2),
  new_amount           numeric(15, 2) not null,
  currency             text not null default 'INR',
  reason               text,
  changed_by           uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now()
);

create index idx_price_history_property on public.property_price_history (property_id, created_at desc);

comment on table public.property_price_history is
  'Append-only price change history. Answers "what was the price on date X?".';

-- ---------------------------------------------------------------------------
-- AMENITIES
-- ---------------------------------------------------------------------------

create table public.amenities (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid references public.tenants (id) on delete cascade, -- NULL = platform
  name                 text not null,
  icon                 text,                               -- Icon name or URL
  category             text,                               -- e.g. "leisure", "infrastructure"
  is_system            boolean not null default false,
  is_active            boolean not null default true,
  created_at           timestamptz not null default now()
);

create index idx_amenities_tenant_id on public.amenities (tenant_id);

-- Seed platform amenities
insert into public.amenities (name, category, is_system) values
  ('Clubhouse',          'leisure',        true),
  ('Swimming Pool',      'leisure',        true),
  ('Gym / Fitness Center','leisure',       true),
  ('Children Play Area', 'leisure',        true),
  ('Jogging Track',      'leisure',        true),
  ('Park / Garden',      'nature',         true),
  ('24x7 Security',      'security',       true),
  ('CCTV Surveillance',  'security',       true),
  ('Gated Community',    'security',       true),
  ('Covered Parking',    'infrastructure', true),
  ('Power Backup',       'infrastructure', true),
  ('Water Supply 24x7',  'infrastructure', true),
  ('Rainwater Harvesting','infrastructure',true),
  ('Solar Panels',       'infrastructure', true),
  ('EV Charging Point',  'infrastructure', true),
  ('Shopping Complex',   'convenience',    true),
  ('School Nearby',      'convenience',    true),
  ('Hospital Nearby',    'convenience',    true),
  ('Temple / Prayer Hall','spiritual',     true),
  ('Community Hall',     'social',         true);

-- ---------------------------------------------------------------------------
-- PROJECT_AMENITIES
-- ---------------------------------------------------------------------------

create table public.project_amenities (
  project_id           uuid not null references public.projects (id) on delete cascade,
  amenity_id           uuid not null references public.amenities (id) on delete restrict,
  notes                text,
  created_at           timestamptz not null default now(),
  primary key (project_id, amenity_id)
);

create index idx_project_amenities_amenity on public.project_amenities (amenity_id);

-- ---------------------------------------------------------------------------
-- PROPERTY_AMENITIES
-- ---------------------------------------------------------------------------

create table public.property_amenities (
  property_id          uuid not null references public.properties (id) on delete cascade,
  amenity_id           uuid not null references public.amenities (id) on delete restrict,
  notes                text,
  created_at           timestamptz not null default now(),
  primary key (property_id, amenity_id)
);

create index idx_property_amenities_amenity on public.property_amenities (amenity_id);

-- ---------------------------------------------------------------------------
-- PROJECT_IMAGES (project-level media)
-- ---------------------------------------------------------------------------

create table public.project_images (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  project_id           uuid not null references public.projects (id) on delete cascade,
  storage_path         text not null,                      -- Supabase storage path
  public_url           text,
  mime_type            text not null,
  file_size_bytes      bigint,
  title                text,
  description          text,
  media_type           text not null default 'image',      -- 'image' | 'video' | 'virtual_tour'
  sort_order           integer not null default 0,
  visibility           public.media_visibility not null default 'public',
  is_cover             boolean not null default false,
  metadata             jsonb not null default '{}',
  uploaded_by          uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

create index idx_project_images_project on public.project_images (project_id, sort_order);

create trigger trg_project_images_updated_at
  before update on public.project_images
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- PROPERTY_IMAGES (property-level media)
-- ---------------------------------------------------------------------------

create table public.property_images (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  property_id          uuid not null references public.properties (id) on delete cascade,
  storage_path         text not null,
  public_url           text,
  mime_type            text not null,
  file_size_bytes      bigint,
  title                text,
  description          text,
  media_type           text not null default 'image',
  sort_order           integer not null default 0,
  visibility           public.media_visibility not null default 'public',
  is_cover             boolean not null default false,
  metadata             jsonb not null default '{}',
  uploaded_by          uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

create index idx_property_images_property on public.property_images (property_id, sort_order);

create trigger trg_property_images_updated_at
  before update on public.property_images
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- PROPERTY_DOCUMENTS (brochures, RERA docs, floor plans, price lists, etc.)
-- ---------------------------------------------------------------------------

create table public.property_documents (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants (id) on delete restrict,
  project_id           uuid references public.projects (id) on delete cascade,   -- project-level doc
  property_id          uuid references public.properties (id) on delete cascade, -- property-level doc
  storage_path         text not null,
  public_url           text,
  mime_type            text not null,
  file_size_bytes      bigint,
  document_type        text not null,                      -- 'brochure'|'floor_plan'|'rera'|'legal'|'price_list'|'other'
  title                text not null,
  description          text,
  visibility           public.media_visibility not null default 'tenant_only',
  metadata             jsonb not null default '{}',
  uploaded_by          uuid references public.users (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),

  constraint chk_property_documents_target
    check (project_id is not null or property_id is not null)
);

create index idx_property_docs_project   on public.property_documents (project_id) where project_id is not null;
create index idx_property_docs_property  on public.property_documents (property_id) where property_id is not null;
create index idx_property_docs_tenant    on public.property_documents (tenant_id);

create trigger trg_property_docs_updated_at
  before update on public.property_documents
  for each row execute function public.set_updated_at();

comment on table public.property_documents is
  'Stores references to documents in Supabase Storage. Not binary blobs.';

-- ---------------------------------------------------------------------------
-- TRIGGER: Auto-add property status history when property.status changes
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_property_status_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if old.status is distinct from new.status then
    insert into public.property_status_history (
      tenant_id, property_id,
      previous_status, new_status,
      changed_by_type
    ) values (
      new.tenant_id, new.id,
      old.status, new.status,
      'system'
    );
  end if;
  return new;
end;
$$;

create trigger trg_properties_status_change
  after update on public.properties
  for each row execute function public.trg_fn_property_status_change();

-- ---------------------------------------------------------------------------
-- TRIGGER: Expire old price records when a new current one is inserted
-- ---------------------------------------------------------------------------

create or replace function public.trg_fn_expire_old_price()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.is_current = true then
    -- Expire any existing current price of the same type for the same property
    update public.property_prices
    set    is_current = false,
           effective_until = new.effective_from - interval '1 day'
    where  property_id = new.property_id
      and  price_type  = new.price_type
      and  is_current  = true
      and  id          <> new.id;

    -- Record price history
    insert into public.property_price_history (
      tenant_id, property_id, property_price_id, price_type,
      old_amount, new_amount, currency
    )
    select
      new.tenant_id, new.property_id, new.id, new.price_type,
      (select amount from public.property_prices
       where property_id = new.property_id
         and price_type  = new.price_type
         and is_current  = false
         and id          <> new.id
       order by effective_from desc limit 1),
      new.amount, new.currency;
  end if;
  return new;
end;
$$;

create trigger trg_property_prices_expire_old
  after insert on public.property_prices
  for each row execute function public.trg_fn_expire_old_price();
