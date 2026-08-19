-- =============================================================================
-- MIGRATION 012: Seed Data (Development Only)
-- Safe, fictitious data for local development and testing.
-- DO NOT RUN IN PRODUCTION.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- TENANT
-- ---------------------------------------------------------------------------

insert into public.tenants (id, name, slug, phone, email, city, state, plan, is_active)
values
  ('00000000-0000-0000-0000-000000000001',
   'ABC Realty', 'abc-realty', '+91-9800000001', 'admin@abcrealty.dev',
   'Jaipur', 'Rajasthan', 'professional', true),
  ('00000000-0000-0000-0000-000000000002',
   'XYZ Developers', 'xyz-developers', '+91-9800000002', 'admin@xyzdev.dev',
   'Gurgaon', 'Haryana', 'trial', true);

-- ---------------------------------------------------------------------------
-- AUTH USERS (Supabase seed — in real env these come from auth.users)
-- ---------------------------------------------------------------------------
-- Note: In Supabase, auth users must be created via the Auth API or dashboard.
-- The UUIDs below are placeholders that must match actual auth.users records.

-- ---------------------------------------------------------------------------
-- APPLICATION USERS
-- ---------------------------------------------------------------------------

insert into public.users (id, tenant_id, auth_user_id, name, email, phone, status)
values
  -- =======================================================================
  -- PLATFORM SUPER ADMIN (tenant_id = NULL — global access, no tenant)
  -- =======================================================================
  ('00000000-0000-0000-0000-100000000000',
   null,                                                   -- No tenant affiliation
   null,                                                   -- Set real auth_user_id in dev
   'Platform Admin',  'superadmin@platform.com',  null,  'active'),

  -- ABC Realty
  ('10000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000001',
   null,                                                   -- Set real auth_user_id in dev
   'Rajesh Sharma',  'rajesh@abcrealty.dev',  '+91-9811110001', 'active'),
  ('10000000-0000-0000-0000-000000000002',
   '00000000-0000-0000-0000-000000000001',
   null,
   'Priya Singh',    'priya@abcrealty.dev',   '+91-9811110002', 'active'),
  ('10000000-0000-0000-0000-000000000003',
   '00000000-0000-0000-0000-000000000001',
   null,
   'Amit Kumar',     'amit@abcrealty.dev',    '+91-9811110003', 'active'),
  -- XYZ Developers
  ('10000000-0000-0000-0000-000000000004',
   '00000000-0000-0000-0000-000000000002',
   null,
   'Sunita Verma',   'sunita@xyzdev.dev',     '+91-9822220001', 'active');

-- ---------------------------------------------------------------------------
-- USER ROLES
-- ---------------------------------------------------------------------------

-- Platform super_admin: tenant_id = NULL (no tenant affiliation)
insert into public.user_roles (tenant_id, user_id, role_id)
select
  null,                                                     -- No tenant
  '00000000-0000-0000-0000-100000000000',
  id
from public.roles where name = 'super_admin' and is_system_role = true;

-- ABC Realty admin
insert into public.user_roles (tenant_id, user_id, role_id)
select
  '00000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  id
from public.roles where name = 'admin';

insert into public.user_roles (tenant_id, user_id, role_id)
select
  '00000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000002',
  id
from public.roles where name = 'sales_manager';

insert into public.user_roles (tenant_id, user_id, role_id)
select
  '00000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000003',
  id
from public.roles where name = 'sales_agent';

insert into public.user_roles (tenant_id, user_id, role_id)
select
  '00000000-0000-0000-0000-000000000002',
  '10000000-0000-0000-0000-000000000004',
  id
from public.roles where name = 'admin';

-- ---------------------------------------------------------------------------
-- SALES AGENTS
-- ---------------------------------------------------------------------------

insert into public.sales_agents (id, tenant_id, user_id, specialization, languages, availability, is_active)
values
  ('20000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000003',
   ARRAY['residential_plot','villa'],
   ARRAY['hi','en'],
   'online', true),
  ('20000000-0000-0000-0000-000000000002',
   '00000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000002',
   ARRAY['apartment','commercial_property'],
   ARRAY['hi','en'],
   'offline', true);

-- ---------------------------------------------------------------------------
-- PROJECT
-- ---------------------------------------------------------------------------

insert into public.projects (id, tenant_id, project_type_id, name, slug, developer_name,
  rera_number, city, state, locality, address_line1,
  latitude, longitude, status, is_public, is_featured,
  price_min, price_max, total_units, available_units,
  launch_date, possession_date, created_by)
values
  ('30000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000001',
   (select id from public.project_types where code = 'plotted_development'),
   'Green Valley Jaipur', 'green-valley-jaipur', 'ABC Realty',
   'RERA/JPN/2025/001', 'Jaipur', 'Rajasthan', 'Jagatpura', 'NH-48, Jagatpura',
   26.8467, 75.8069,
   'launched', true, true,
   3500000, 8500000, 250, 180,
   '2025-01-15', '2027-06-30',
   '10000000-0000-0000-0000-000000000001');

-- ---------------------------------------------------------------------------
-- PROPERTIES
-- ---------------------------------------------------------------------------

insert into public.properties (
  id, tenant_id, project_id, property_type_id, property_code, unit_number,
  plot_area, area_unit, facing, is_corner, base_price, offer_price, currency, status, is_public
)
select
  gen_random_uuid(),
  '00000000-0000-0000-0000-000000000001',
  '30000000-0000-0000-0000-000000000001',
  (select id from public.property_types where code = 'residential_plot'),
  'P-' || lpad(n::text, 3, '0'),
  'P-' || lpad(n::text, 3, '0'),
  case when n % 3 = 0 then 200 when n % 3 = 1 then 150 else 100 end,  -- plot_area (gaj)
  'gaj',
  (array['north','south','east','west'])[1 + (n % 4)],
  (n % 5 = 0),
  case when n % 3 = 0 then 7200000 when n % 3 = 1 then 5400000 else 3600000 end,
  null,
  'INR',
  case when n <= 70 then 'available'
       when n <= 80 then 'hold'
       when n <= 85 then 'reserved'
       else 'available' end,
  true
from generate_series(1, 20) as n;

-- ---------------------------------------------------------------------------
-- PROPERTY PRICES
-- ---------------------------------------------------------------------------

insert into public.property_prices (tenant_id, property_id, price_type, amount, currency, is_current)
select
  p.tenant_id,
  p.id,
  'base_price',
  p.base_price,
  'INR',
  true
from public.properties p
where p.tenant_id = '00000000-0000-0000-0000-000000000001'
  and p.base_price is not null;

-- ---------------------------------------------------------------------------
-- PROJECT AMENITIES
-- ---------------------------------------------------------------------------

insert into public.project_amenities (project_id, amenity_id)
select
  '30000000-0000-0000-0000-000000000001',
  id
from public.amenities
where name in ('Clubhouse','Swimming Pool','Gym / Fitness Center','Park / Garden','24x7 Security','Gated Community','Covered Parking','Power Backup');

-- ---------------------------------------------------------------------------
-- AGENT CONFIG
-- ---------------------------------------------------------------------------

insert into public.agent_configs (
  id, tenant_id, name, description, version, is_active,
  language, stt_provider, stt_model, stt_language,
  llm_provider, llm_model, llm_temperature,
  tts_provider, tts_model,
  system_prompt, created_by
)
values (
  '40000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  'Hindi Sales Agent v1',
  'Primary AI sales agent for ABC Realty - Hindi language outbound calls',
  1, true,
  'hi', 'deepgram', 'nova-3', 'hi',
  'google', 'gemma-4-31b-it', 0.7,
  'cartesia', 'sonic-3',
  'Aap ABC Realty ke taraf se baat kar rahe hain. Aapka kaam hai leads ko qualify karna aur site visit book karna.',
  '10000000-0000-0000-0000-000000000001'
);

-- Campaign
insert into public.campaigns (
  id, tenant_id, name, description, type, status,
  agent_config_id, max_attempts, calling_start_time, calling_end_time,
  created_by
)
values (
  '50000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  'August Jaipur Plot Campaign',
  'Outbound AI calls to Jaipur plot enquiry leads',
  'ai_outbound', 'active',
  '40000000-0000-0000-0000-000000000001',
  3, '09:30', '19:30',
  '10000000-0000-0000-0000-000000000001'
);

-- ---------------------------------------------------------------------------
-- CUSTOMERS
-- ---------------------------------------------------------------------------

insert into public.customers (id, tenant_id, full_name, phone, email, city, state, preferred_language)
values
  ('60000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000001',
   'Ravi Prakash', '+91-9900000001', 'ravi.p@example.dev', 'Jaipur', 'Rajasthan', 'hi'),
  ('60000000-0000-0000-0000-000000000002',
   '00000000-0000-0000-0000-000000000001',
   'Meena Gupta', '+91-9900000002', 'meena.g@example.dev', 'Delhi', 'Delhi', 'hi'),
  ('60000000-0000-0000-0000-000000000003',
   '00000000-0000-0000-0000-000000000001',
   'Suresh Patel', '+91-9900000003', null, 'Ahmedabad', 'Gujarat', 'hi'),
  -- XYZ tenant (isolated)
  ('60000000-0000-0000-0000-000000000004',
   '00000000-0000-0000-0000-000000000002',
   'Anita Joshi', '+91-9900000004', null, 'Gurgaon', 'Haryana', 'hi');

-- ---------------------------------------------------------------------------
-- CUSTOMER PREFERENCES
-- ---------------------------------------------------------------------------

insert into public.customer_preferences (tenant_id, customer_id, property_types, preferred_cities, budget_min, budget_max, finance_requirement, purchase_timeline)
values
  ('00000000-0000-0000-0000-000000000001',
   '60000000-0000-0000-0000-000000000001',
   ARRAY['residential_plot','villa'], ARRAY['Jaipur'],
   3500000, 7000000, 'bank_loan', '6_months'),
  ('00000000-0000-0000-0000-000000000001',
   '60000000-0000-0000-0000-000000000002',
   ARRAY['apartment'], ARRAY['Jaipur','Gurgaon'],
   5000000, 12000000, 'self_funded', '3_months');

-- ---------------------------------------------------------------------------
-- LEADS
-- ---------------------------------------------------------------------------

insert into public.leads (
  id, tenant_id, customer_id, lead_source_id, campaign_id,
  property_type_id, purpose, preferred_city, budget_min, budget_max,
  status, sales_stage, lead_score, assigned_sales_agent_id
)
values
  ('70000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000001',
   '60000000-0000-0000-0000-000000000001',
   (select id from public.lead_sources where code = 'facebook' and tenant_id is null),
   '50000000-0000-0000-0000-000000000001',
   (select id from public.property_types where code = 'residential_plot'),
   'investment', 'Jaipur', 3500000, 7000000,
   'active', 'contacted', 72,
   '20000000-0000-0000-0000-000000000001'),
  ('70000000-0000-0000-0000-000000000002',
   '00000000-0000-0000-0000-000000000001',
   '60000000-0000-0000-0000-000000000002',
   (select id from public.lead_sources where code = 'website' and tenant_id is null),
   null,
   (select id from public.property_types where code = 'apartment'),
   'end_use', 'Jaipur', 5000000, 12000000,
   'new', 'new', 15,
   null),
  ('70000000-0000-0000-0000-000000000003',
   '00000000-0000-0000-0000-000000000001',
   '60000000-0000-0000-0000-000000000001',
   (select id from public.lead_sources where code = 'referral' and tenant_id is null),
   null,
   (select id from public.property_types where code = 'villa'),
   'end_use', 'Jaipur', 8000000, 15000000,
   'active', 'qualified', 85,
   '20000000-0000-0000-0000-000000000001');

-- Lead property interests
insert into public.lead_property_interests (tenant_id, lead_id, project_id, interest_level, is_primary)
values
  ('00000000-0000-0000-0000-000000000001',
   '70000000-0000-0000-0000-000000000001',
   '30000000-0000-0000-0000-000000000001',
   'high', true),
  ('00000000-0000-0000-0000-000000000001',
   '70000000-0000-0000-0000-000000000003',
   '30000000-0000-0000-0000-000000000001',
   'very_high', true);

-- Campaign leads
insert into public.campaign_leads (tenant_id, campaign_id, lead_id, status, priority, attempt_count)
values
  ('00000000-0000-0000-0000-000000000001',
   '50000000-0000-0000-0000-000000000001',
   '70000000-0000-0000-0000-000000000001',
   'called', 5, 1),
  ('00000000-0000-0000-0000-000000000001',
   '50000000-0000-0000-0000-000000000001',
   '70000000-0000-0000-0000-000000000002',
   'pending', 5, 0);

-- ---------------------------------------------------------------------------
-- SAMPLE CALL
-- ---------------------------------------------------------------------------

insert into public.calls (
  id, tenant_id, lead_id, customer_id, campaign_id,
  agent_config_id, direction, provider, provider_call_id, phone_from, phone_to,
  status, outcome,
  initiated_at, answered_at, ended_at, duration_seconds, talk_time_seconds,
  interruption_count, avg_stt_latency_ms, avg_llm_latency_ms, avg_tts_latency_ms,
  llm_input_tokens, llm_output_tokens, call_summary
)
values (
  '80000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  '70000000-0000-0000-0000-000000000001',
  '60000000-0000-0000-0000-000000000001',
  '50000000-0000-0000-0000-000000000001',
  '40000000-0000-0000-0000-000000000001',
  'outbound', 'supaphone', 'SPHN-20250811-001', '+918000000001', '+919900000001',
  'completed', 'qualified',
  now() - interval '2 hours',
  now() - interval '2 hours' + interval '15 seconds',
  now() - interval '1 hour 45 minutes',
  840, 710,
  2, 320, 1200, 280,
  2400, 380,
  'Ravi is interested in a 200 gaj plot in Jagatpura. Budget 50-70 lakh. Wants to visit site this weekend.'
);

-- Lead score event
insert into public.lead_score_events (tenant_id, lead_id, call_id, previous_score, new_score, reason, scoring_model)
values (
  '00000000-0000-0000-0000-000000000001',
  '70000000-0000-0000-0000-000000000001',
  '80000000-0000-0000-0000-000000000001',
  45, 72, 'Qualified on call. High interest in 200gaj plot. Site visit intent confirmed.', 'ai_v1'
);

-- Activity
insert into public.activities (tenant_id, customer_id, lead_id, call_id, activity_type, actor_type, title, description)
values (
  '00000000-0000-0000-0000-000000000001',
  '60000000-0000-0000-0000-000000000001',
  '70000000-0000-0000-0000-000000000001',
  '80000000-0000-0000-0000-000000000001',
  'call_completed', 'ai_agent',
  'AI Call Completed - Qualified',
  'AI agent completed outbound call. Lead qualified. Site visit interest expressed.'
);

-- Conversation summary
insert into public.conversation_summaries (
  tenant_id, call_id, lead_id, customer_id,
  summary_text, customer_intent, customer_emotion,
  extracted_budget_min, extracted_budget_max, extracted_city,
  interest_level, buying_probability, site_visit_probability, suggested_lead_score,
  next_best_action, generated_by_model
)
values (
  '00000000-0000-0000-0000-000000000001',
  '80000000-0000-0000-0000-000000000001',
  '70000000-0000-0000-0000-000000000001',
  '60000000-0000-0000-0000-000000000001',
  'Ravi confirmed interest in 200 gaj plot. Budget 50-70 lakh. Site visit requested for this weekend.',
  'site_visit_request',
  'positive',
  5000000, 7000000, 'Jaipur',
  'very_high', 72, 85, 72,
  'Book site visit for this weekend. Send plot brochure on WhatsApp.',
  'google/gemma-4-31b-it'
);
