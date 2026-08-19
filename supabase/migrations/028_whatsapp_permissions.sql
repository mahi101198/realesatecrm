-- =============================================================================
-- MIGRATION 028: WhatsApp Messaging Permissions
--
-- Adds the permission family for the new app/whatsapp/ domain module
-- (Superfone "Dragonfly" WhatsApp Business API integration). Follows the
-- exact seeding pattern of migrations 023-027.
--
-- whatsapp_message.read / whatsapp_template.read are granted broadly, same
-- tier as property.read/location.read (ordinary CRM read access).
-- whatsapp_message.create (sending a message to a customer) is granted only
-- to roles that actually work leads/customers day to day -- not manager/
-- viewer, which are reporting/read-only roles elsewhere in this schema too.
-- =============================================================================

insert into public.permissions (code, name, description, resource, action) values
  ('whatsapp_message.read',   'Read WhatsApp Messages',  'View WhatsApp message history for customers/leads.', 'whatsapp_message',  'read'),
  ('whatsapp_message.create', 'Send WhatsApp Messages',  'Send a WhatsApp message to a customer.',             'whatsapp_message',  'create'),
  ('whatsapp_template.read',  'Read WhatsApp Templates', 'View approved/pending WhatsApp message templates.',  'whatsapp_template', 'read')
on conflict (code) do nothing;

-- admin, sales_manager: full read + send.
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name in ('admin', 'sales_manager') and r.is_system_role = true
  and  p.code in ('whatsapp_message.read', 'whatsapp_message.create', 'whatsapp_template.read')
on conflict do nothing;

-- sales_agent: full read + send (front-line customer communication).
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name = 'sales_agent' and r.is_system_role = true
  and  p.code in ('whatsapp_message.read', 'whatsapp_message.create', 'whatsapp_template.read')
on conflict do nothing;

-- manager, viewer: read-only, matching the reporting-access distribution
-- established in 023/024/025.
insert into public.role_permissions (role_id, permission_id)
select r.id, p.id
from   public.roles r, public.permissions p
where  r.name in ('manager', 'viewer') and r.is_system_role = true
  and  p.code in ('whatsapp_message.read', 'whatsapp_template.read')
on conflict do nothing;
