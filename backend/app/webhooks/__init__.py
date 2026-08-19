"""Inbound Webhook Endpoints Package.

Every webhook here follows skills/system.md rule #30: verify authenticity
first (fail closed), validate payload defensively, persist the raw delivery
for idempotency/audit (public.webhook_events), then process. No webhook
payload field is ever trusted for tenant_id -- tenant context is always
derived server-side from our own records.
"""
