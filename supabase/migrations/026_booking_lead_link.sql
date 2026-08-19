-- =============================================================================
-- MIGRATION 026: Booking -> Lead Link
--
-- Closes the loop the business owner explicitly asked about: "if property
-- got sold then lead completed". property_sales.create_sale (app layer)
-- needs a way to know which lead originated a sale, so it can mark that
-- lead status = 'converted' at the moment the sale closes.
--
-- Minimal, additive: one nullable column on property_bookings, tenant-safe
-- composite FK to leads, same conventions as 023-025.
-- =============================================================================

alter table public.property_bookings
  add column lead_id uuid;

alter table public.property_bookings
  add constraint fk_property_bookings_lead_tenant
  foreign key (tenant_id, lead_id) references public.leads (tenant_id, id) on delete set null;

create index idx_property_bookings_lead on public.property_bookings (tenant_id, lead_id) where lead_id is not null;

comment on column public.property_bookings.lead_id is
  'Optional link to the lead that originated this booking. When a sale '
  'referencing this booking is created, the app layer marks this lead '
  'status = ''converted'' -- closing the loop from enquiry to sale.';
