-- =============================================================================
-- MIGRATION 029: Multi-Tenant Direct-Meta WhatsApp Configuration
--
-- Per-tenant Meta WhatsApp Cloud API credentials, replacing the Superfone-
-- routed WhatsApp integration removed in this same body of work. One row
-- per tenant (a tenant has exactly one WABA/phone number in this phase).
--
-- access_token_encrypted / app_secret_encrypted are Fernet-encrypted by
-- app/integrations/whatsapp/crypto.py before insert; this table never
-- stores plaintext secrets. verify_token is compared in plaintext (it is
-- Meta's own low-sensitivity handshake value, not a cryptographic secret --
-- same trust level as the existing Superfone SFVoPI query-token pattern).
-- =============================================================================

create table public.whatsapp_tenant_configs (
  tenant_id               uuid primary key references public.tenants (id) on delete cascade,
  waba_id                 text not null,
  phone_number_id         text not null,
  verify_token            text not null,
  access_token_encrypted  text not null,
  app_secret_encrypted    text not null,
  is_active               boolean not null default true,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),

  constraint uq_whatsapp_tenant_configs_phone_number_id unique (phone_number_id)
);

create trigger trg_whatsapp_tenant_configs_updated_at
  before update on public.whatsapp_tenant_configs
  for each row execute function public.set_updated_at();

comment on table public.whatsapp_tenant_configs is
  'One row per tenant: Meta WhatsApp Cloud API credentials (WABA/phone '
  'number/tokens), encrypted at rest. Read only by app/integrations/whatsapp/.';

-- ---------------------------------------------------------------------------
-- RLS: service_role only. This table holds encrypted secrets; unlike other
-- tenant-owned tables, no `authenticated` policy is added at all -- there is
-- no legitimate reason for a direct (non-FastAPI) client to read even the
-- ciphertext columns. This backend always connects as service_role (see
-- app/tenants/service.py's docstring for the established precedent).
-- ---------------------------------------------------------------------------

alter table public.whatsapp_tenant_configs enable row level security;

create policy "whatsapp_tenant_configs: service_role full access"
  on public.whatsapp_tenant_configs for all
  to service_role using (true) with check (true);

-- ---------------------------------------------------------------------------
-- Permission: platform.whatsapp_config.manage -- super_admin only, follows
-- migration 018's exact seeding pattern for platform.* permissions.
-- ---------------------------------------------------------------------------

insert into public.permissions (code, name, description, resource, action) values
  ('platform.whatsapp_config.manage', 'Manage Tenant WhatsApp Config',
   'Create, rotate, or view (metadata only) a tenant''s Meta WhatsApp credentials.',
   'platform', 'whatsapp_config.manage')
on conflict (code) do nothing;

insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'super_admin'
  and  r.is_system_role = true
  and  p.code = 'platform.whatsapp_config.manage'
on conflict do nothing;
