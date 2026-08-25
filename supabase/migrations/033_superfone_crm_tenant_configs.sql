-- =============================================================================
-- MIGRATION 033: Per-Tenant Superfone CRM Webhook Configuration
--
-- Closes a real gap: the Superfone CRM event-notification webhook
-- (ALL_CALLS/MISSED_CALL/CDR_RECORDING_AVAILABLE/CDR_SUMMARY_READY, see
-- app/webhooks/superfone/service.py) was a single global endpoint
-- authenticated by one shared bearer secret, with no tenant identifier
-- anywhere in the request. A "cold" CDR event (no calls row already
-- correlated by cdr_uuid) could therefore never be safely attributed to a
-- tenant -- calls.tenant_id is NOT NULL, so the code correctly refused to
-- guess and just logged the event to webhook_events.
--
-- This migration lets each tenant register its own Superfone CRM dashboard
-- automation against its own URL (/webhooks/superfone/crm/{tenant_id}/
-- events/{event_type}) with its own bearer secret, exactly mirroring the
-- existing per-tenant pattern for direct-Meta WhatsApp (migration 029,
-- whatsapp_tenant_configs) and SFVoPI (this backend's own outbound calling).
--
-- Unlike whatsapp_tenant_configs, this table stores a HASH, not a
-- reversibly-encrypted secret: this bearer secret is only ever compared
-- against an incoming header, never read back to authenticate an outbound
-- call, so a one-way SHA-256 digest (app/webhooks/superfone/security.py
-- hash_secret) is the stronger choice -- even a full DB read discloses
-- nothing usable.
-- =============================================================================

create table public.superfone_crm_tenant_configs (
  tenant_id           uuid primary key references public.tenants (id) on delete cascade,
  bearer_secret_hash  text not null,
  is_active           boolean not null default true,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create trigger trg_superfone_crm_tenant_configs_updated_at
  before update on public.superfone_crm_tenant_configs
  for each row execute function public.set_updated_at();

comment on table public.superfone_crm_tenant_configs is
  'One row per tenant: the SHA-256 hash of the bearer secret that tenant''s '
  'Superfone CRM dashboard automation presents on '
  '/webhooks/superfone/crm/{tenant_id}/events/{event_type}. Never stores the '
  'plaintext secret -- only a one-way hash, since it is compare-only.';

-- ---------------------------------------------------------------------------
-- RLS: service_role only, same reasoning as whatsapp_tenant_configs -- this
-- table holds a credential; there is no legitimate reason for a direct
-- (non-FastAPI) client to read even the hashed column.
-- ---------------------------------------------------------------------------

alter table public.superfone_crm_tenant_configs enable row level security;

create policy "superfone_crm_tenant_configs: service_role full access"
  on public.superfone_crm_tenant_configs for all
  to service_role using (true) with check (true);

-- ---------------------------------------------------------------------------
-- Permission: platform.superfone_crm_config.manage -- super_admin only,
-- follows migration 029's exact seeding pattern for platform.* permissions.
-- ---------------------------------------------------------------------------

insert into public.permissions (code, name, description, resource, action) values
  ('platform.superfone_crm_config.manage', 'Manage Tenant Superfone CRM Webhook Config',
   'Create, rotate, or view (metadata only) a tenant''s Superfone CRM webhook bearer secret.',
   'platform', 'superfone_crm_config.manage')
on conflict (code) do nothing;

insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'super_admin'
  and  r.is_system_role = true
  and  p.code = 'platform.superfone_crm_config.manage'
on conflict do nothing;
