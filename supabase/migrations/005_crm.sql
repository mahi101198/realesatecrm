-- =============================================================================
-- MIGRATION 005: CRM Tables
-- lead_sources → customers → customer_preferences → customer_notes
-- leads → lead_property_interests → lead_notes
-- campaigns → campaign_leads
-- =============================================================================

-- ---------------------------------------------------------------------------
-- LEAD_SOURCES
-- ---------------------------------------------------------------------------
-- Configurable per-tenant. Allows "facebook", "website", custom sources.
-- ---------------------------------------------------------------------------

create table public.lead_sources (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid references public.tenants (id) on delete cascade, -- NULL = platform default
  name                 text not null,
  code                 text not null,
  description          text,
  is_system            boolean not null default false,
  is_active            boolean not null default true,
  sort_order           integer not null default 0,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),

  constraint uq_lead_sources_tenant_code unique (tenant_id, code)
);

create index idx_lead_sources_tenant_id on public.lead_sources (tenant_id);

create trigger trg_lead_sources_updated_at
  before update on public.lead_sources
  for each row execute function public.set_updated_at();

-- Platform-level default sources
insert into public.lead_sources (name, code, is_system) values
  ('Website',      'website',    true),
  ('Facebook',     'facebook',   true),
  ('Instagram',    'instagram',  true),
  ('Google',       'google',     true),
  ('WhatsApp',     'whatsapp',   true),
  ('Referral',     'referral',   true),
  ('Manual Entry', 'manual',     true),
  ('CSV Import',   'import',     true),
  ('Walk In',      'walk_in',    true),
  ('Phone',        'phone',      true),
  ('Other',        'other',      true);

-- ---------------------------------------------------------------------------
-- CUSTOMERS
-- ---------------------------------------------------------------------------
-- One record per real person regardless of how many enquiries they make.
-- Deduplication key: (tenant_id, phone).
-- ---------------------------------------------------------------------------

create table public.customers (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  -- Identity
  full_name             text not null,
  first_name            text,
  last_name             text,
  phone                 text not null,
  alternate_phone       text,
  email                 text,
  gender                text,
  date_of_birth         date,
  -- Address
  address_line1         text,
  city                  text,
  state                 text,
  country               text not null default 'India',
  pincode               text,
  -- Communication preferences
  preferred_language    text not null default 'hi',
  preferred_contact_time text,                             -- e.g. "10:00-13:00"
  do_not_call           boolean not null default false,
  do_not_whatsapp       boolean not null default false,
  do_not_email          boolean not null default false,
  whatsapp_opted_in     boolean not null default false,
  -- Source
  lead_source_id        uuid references public.lead_sources (id) on delete set null,
  referred_by           uuid references public.customers (id) on delete set null,
  -- Metadata
  metadata              jsonb not null default '{}',
  -- Timestamps
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  deleted_at            timestamptz,

  constraint uq_customers_tenant_phone unique (tenant_id, phone)
);

create index idx_customers_tenant_id       on public.customers (tenant_id);
create index idx_customers_tenant_phone    on public.customers (tenant_id, phone);
create index idx_customers_tenant_email    on public.customers (tenant_id, email) where email is not null;
create index idx_customers_created_at      on public.customers (tenant_id, created_at desc);
create index idx_customers_name_trgm       on public.customers using gin (full_name gin_trgm_ops);

create trigger trg_customers_updated_at
  before update on public.customers
  for each row execute function public.set_updated_at();

comment on table public.customers is
  'One record per real person. Multiple leads can reference the same customer.';

-- ---------------------------------------------------------------------------
-- CUSTOMER_PREFERENCES
-- ---------------------------------------------------------------------------
-- Long-lived preferences not tied to a specific lead.
-- ---------------------------------------------------------------------------

create table public.customer_preferences (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  customer_id           uuid not null references public.customers (id) on delete cascade,
  -- Property preferences
  property_types        text[],                            -- Interested property type codes
  preferred_cities      text[],
  preferred_localities  text[],
  purpose               public.purpose,
  budget_min            numeric(15, 2),
  budget_max            numeric(15, 2),
  area_min              numeric(12, 4),
  area_max              numeric(12, 4),
  area_unit             public.area_unit default 'sqft',
  bedrooms_min          integer,
  bedrooms_max          integer,
  -- Finance
  finance_requirement   public.finance_requirement,
  -- Timeline
  purchase_timeline     text,                              -- e.g. "3_months", "6_months", "1_year"
  -- Communication
  preferred_contact_days text[],                           -- e.g. ['monday','saturday']
  preferred_contact_time text,
  notes                 text,
  -- Timestamps
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_customer_preferences_customer unique (customer_id),
  constraint chk_customer_prefs_budget
    check (budget_min is null or budget_max is null or budget_max >= budget_min),
  constraint chk_customer_prefs_area
    check (area_min is null or area_max is null or area_max >= area_min),
  constraint chk_customer_prefs_budget_min check (budget_min is null or budget_min >= 0),
  constraint chk_customer_prefs_budget_max check (budget_max is null or budget_max >= 0)
);

create index idx_customer_prefs_tenant    on public.customer_preferences (tenant_id);
create index idx_customer_prefs_customer  on public.customer_preferences (customer_id);

create trigger trg_customer_preferences_updated_at
  before update on public.customer_preferences
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- CUSTOMER_NOTES
-- ---------------------------------------------------------------------------

create table public.customer_notes (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  customer_id           uuid not null references public.customers (id) on delete cascade,
  author_id             uuid not null references public.users (id) on delete restrict,
  note_text             text not null,
  is_pinned             boolean not null default false,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index idx_customer_notes_customer on public.customer_notes (customer_id, created_at desc);

create trigger trg_customer_notes_updated_at
  before update on public.customer_notes
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- LEADS
-- ---------------------------------------------------------------------------
-- A lead is a specific sales opportunity for a customer.
-- One customer can have multiple leads.
-- ---------------------------------------------------------------------------

create sequence public.lead_number_seq;

create table public.leads (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  -- Identity
  lead_number           text not null,                     -- Human-readable e.g. "LD-00001"
  -- Source
  lead_source_id        uuid references public.lead_sources (id) on delete set null,
  campaign_id           uuid,                              -- FK added after campaigns table
  -- Requirements (lead-specific snapshot)
  property_type_id      uuid references public.property_types (id) on delete set null,
  purpose               public.purpose,
  preferred_city        text,
  preferred_locality    text,
  budget_min            numeric(15, 2),
  budget_max            numeric(15, 2),
  area_min              numeric(12, 4),
  area_max              numeric(12, 4),
  area_unit             public.area_unit default 'sqft',
  bedrooms              integer,
  timeline              text,
  finance_requirement   public.finance_requirement,
  -- Status & scoring
  status                public.lead_status not null default 'new',
  sales_stage           public.sales_stage not null default 'new',
  lead_score            integer not null default 0,
  interest_level        public.interest_level,
  -- Assignment
  assigned_sales_agent_id uuid references public.sales_agents (id) on delete set null,
  -- Timestamps
  first_contacted_at    timestamptz,
  last_contacted_at     timestamptz,
  next_follow_up_at     timestamptz,
  converted_at          timestamptz,
  lost_at               timestamptz,
  lost_reason           text,
  -- Metadata
  notes                 text,
  metadata              jsonb not null default '{}',
  created_by            uuid references public.users (id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  deleted_at            timestamptz,

  constraint uq_leads_tenant_number unique (tenant_id, lead_number),
  constraint chk_leads_score      check (lead_score between 0 and 100),
  constraint chk_leads_budget_min check (budget_min is null or budget_min >= 0),
  constraint chk_leads_budget_max check (budget_max is null or budget_max >= 0),
  constraint chk_leads_budget     check (budget_min is null or budget_max is null or budget_max >= budget_min)
);

create index idx_leads_tenant_id           on public.leads (tenant_id);
create index idx_leads_customer_id         on public.leads (tenant_id, customer_id);
create index idx_leads_status              on public.leads (tenant_id, status);
create index idx_leads_stage               on public.leads (tenant_id, sales_stage);
create index idx_leads_score               on public.leads (tenant_id, lead_score desc);
create index idx_leads_assigned_agent      on public.leads (tenant_id, assigned_sales_agent_id);
create index idx_leads_next_followup       on public.leads (tenant_id, next_follow_up_at) where next_follow_up_at is not null;
create index idx_leads_created_at          on public.leads (tenant_id, created_at desc);
create index idx_leads_campaign_id         on public.leads (tenant_id, campaign_id) where campaign_id is not null;

create trigger trg_leads_updated_at
  before update on public.leads
  for each row execute function public.set_updated_at();

comment on table public.leads is
  'A specific sales opportunity. One customer can have many leads over time.';

-- ---------------------------------------------------------------------------
-- LEAD NUMBER GENERATION FUNCTION
-- ---------------------------------------------------------------------------

create or replace function public.generate_lead_number(p_tenant_id uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_num integer;
begin
  v_num := nextval('public.lead_number_seq');
  return 'LD-' || lpad(v_num::text, 6, '0');
end;
$$;

comment on function public.generate_lead_number(uuid) is
  'Generates a sequential human-readable lead number like LD-000001.';

-- Auto-assign lead number on insert if not provided
create or replace function public.trg_fn_lead_number()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.lead_number is null or new.lead_number = '' then
    new.lead_number := public.generate_lead_number(new.tenant_id);
  end if;
  return new;
end;
$$;

create trigger trg_leads_auto_number
  before insert on public.leads
  for each row execute function public.trg_fn_lead_number();

-- ---------------------------------------------------------------------------
-- LEAD_PROPERTY_INTERESTS
-- ---------------------------------------------------------------------------
-- A lead may be interested in multiple specific properties.
-- ---------------------------------------------------------------------------

create table public.lead_property_interests (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  project_id            uuid not null references public.projects (id) on delete restrict,
  property_id           uuid references public.properties (id) on delete set null, -- Specific unit (optional)
  interest_level        public.interest_level not null default 'medium',
  is_primary            boolean not null default false,
  notes                 text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_lead_property_interest unique (lead_id, project_id, property_id)
);

create index idx_lead_prop_interests_lead    on public.lead_property_interests (lead_id);
create index idx_lead_prop_interests_project on public.lead_property_interests (project_id);
create index idx_lead_prop_interests_property on public.lead_property_interests (property_id) where property_id is not null;

create trigger trg_lead_property_interests_updated_at
  before update on public.lead_property_interests
  for each row execute function public.set_updated_at();

comment on table public.lead_property_interests is
  'Many-to-many between leads and properties/projects with interest level.';

-- ---------------------------------------------------------------------------
-- LEAD_NOTES
-- ---------------------------------------------------------------------------

create table public.lead_notes (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  customer_id           uuid not null references public.customers (id) on delete restrict,
  author_id             uuid not null references public.users (id) on delete restrict,
  note_text             text not null,
  is_pinned             boolean not null default false,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index idx_lead_notes_lead on public.lead_notes (lead_id, created_at desc);

create trigger trg_lead_notes_updated_at
  before update on public.lead_notes
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- CAMPAIGNS
-- ---------------------------------------------------------------------------

create table public.campaigns (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  name                  text not null,
  description           text,
  type                  public.campaign_type not null default 'ai_outbound',
  status                public.campaign_status not null default 'draft',
  lead_source_id        uuid references public.lead_sources (id) on delete set null,
  agent_config_id       uuid,                              -- FK added after agent_configs table
  -- Schedule & limits
  start_at              timestamptz,
  end_at                timestamptz,
  max_attempts          integer not null default 3,
  retry_interval_hours  integer not null default 24,
  -- Calling hours (in IST, stored as text ranges)
  calling_start_time    time not null default '09:00',     -- Local start time
  calling_end_time      time not null default '20:00',     -- Local end time
  calling_days          integer[] not null default ARRAY[1,2,3,4,5,6], -- 0=Sun, 6=Sat
  -- Targeting
  target_property_type_id uuid references public.property_types (id) on delete set null,
  target_project_id     uuid references public.projects (id) on delete set null,
  -- Stats (denormalized for dashboard performance)
  total_leads           integer not null default 0,
  total_calls           integer not null default 0,
  connected_calls       integer not null default 0,
  qualified_leads       integer not null default 0,
  -- Creator
  created_by            uuid references public.users (id) on delete set null,
  settings              jsonb not null default '{}',       -- Additional flexible settings
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  deleted_at            timestamptz,

  constraint chk_campaigns_attempts check (max_attempts > 0),
  constraint chk_campaigns_retry    check (retry_interval_hours > 0),
  constraint chk_campaigns_dates    check (start_at is null or end_at is null or end_at > start_at)
);

create index idx_campaigns_tenant_id   on public.campaigns (tenant_id);
create index idx_campaigns_status      on public.campaigns (tenant_id, status);
create index idx_campaigns_created_at  on public.campaigns (tenant_id, created_at desc);

create trigger trg_campaigns_updated_at
  before update on public.campaigns
  for each row execute function public.set_updated_at();

comment on table public.campaigns is
  'Marketing or calling campaign. Leads are enrolled via campaign_leads.';

-- Add FK from leads to campaigns (now that campaigns exists)
alter table public.leads
  add constraint fk_leads_campaign_id
  foreign key (campaign_id) references public.campaigns (id) on delete set null;

-- ---------------------------------------------------------------------------
-- CAMPAIGN_LEADS
-- ---------------------------------------------------------------------------
-- Many-to-many between campaigns and leads with call attempt tracking.
-- ---------------------------------------------------------------------------

create table public.campaign_leads (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  campaign_id           uuid not null references public.campaigns (id) on delete cascade,
  lead_id               uuid not null references public.leads (id) on delete cascade,
  status                public.campaign_lead_status not null default 'pending',
  priority              integer not null default 5,         -- 1 (highest) to 10 (lowest)
  attempt_count         integer not null default 0,
  last_attempt_at       timestamptz,
  next_attempt_at       timestamptz,
  added_by              uuid references public.users (id) on delete set null,
  added_at              timestamptz not null default now(),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_campaign_leads unique (campaign_id, lead_id),
  constraint chk_campaign_leads_attempts check (attempt_count >= 0)
);

create index idx_campaign_leads_campaign    on public.campaign_leads (campaign_id, status);
create index idx_campaign_leads_lead        on public.campaign_leads (lead_id);
create index idx_campaign_leads_next_attempt on public.campaign_leads (next_attempt_at)
  where status = 'pending' or status = 'callback';

create trigger trg_campaign_leads_updated_at
  before update on public.campaign_leads
  for each row execute function public.set_updated_at();

comment on table public.campaign_leads is
  'Lead enrollment in a campaign with call attempt tracking.';
