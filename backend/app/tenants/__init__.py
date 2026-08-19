"""Tenant Admin Domain Package.

Deliberately minimal: GET + PATCH only. Tenant CREATE is NOT exposed here --
migration 018's RLS policy ("tenants: super_admin can insert") and the
platform.tenant.create permission already establish tenant provisioning as a
super_admin/platform-level operation, not a normal tenant-admin-facing API
action. Nothing in this module changes that.
"""
