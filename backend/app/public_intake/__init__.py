"""Public (Unauthenticated) Lead Intake Domain Package.

Deliberately separate from app/leads/ and app/customers/: this is a different
auth model entirely (no RequestContext, no permission gate), a different
identity-resolution flow (find-or-create by phone, not staff-driven create),
and carries its own rate limiting. See router.py for the full reasoning.
"""
