"""Superfone Telephony Integration Package.

Two separate API surfaces, deliberately kept as two separate client classes
with two separate configured credential pairs (they may require separate
product activation on Superfone's side, per verified API documentation):

  - SFVoPIClient    -- the AI voice platform (place outbound AI calls).
  - SuperfoneCRMClient -- human-to-human click-to-call for sales-agent handoffs.

Neither client talks to a database or knows about tenants/leads/customers --
that's the calling service's job. This package only knows how to speak
Superfone's REST API.
"""
