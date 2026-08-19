# Multi-Tenant Direct-Meta WhatsApp Integration — Design

Status: approved, not yet implemented
Date: 2026-08-19

## Context

The previous session built WhatsApp messaging routed through Superfone's
"Dragonfly" product (`app/integrations/superfone/whatsapp_client.py`,
`app/whatsapp/*`, a `SUPERFONE_WHATSAPP_WEBHOOK_SHARED_SECRET`-gated webhook
stream). That work is being discarded: this platform is multi-tenant, and
WhatsApp must go directly to Meta's Cloud API with **per-tenant** credentials
(WABA ID, phone number ID, access token, app secret), not a single
Superfone-mediated account.

`app/main.py` currently has a broken import
(`app.webhooks.superfone.whatsapp.router`, a module that was never created)
which prevents the app from starting at all. This work fixes that as a side
effect of removing the Superfone WhatsApp code.

A separate, already-live product (`whatsapp_busness_dashboard`, a standalone
Next.js + Supabase app, not part of this repo) currently handles WhatsApp
for exactly one tenant via its own registered Meta App/WABA
(`789424670144149` / `770971286099252`) and its own webhook subscription. It
must NOT be touched or have its webhook subscription re-registered — doing
so would deregister its callback and silently break it, since Meta's
subscribe call replaces a topic's entire field list rather than merging.
That product's AI reply agent has a stubbed, unfinished integration point
(`triggerCallAgent` in its `lib/ai/tools.ts`) that POSTs `{phone, reason}` to
an external "calling agent" URL with a bearer key when a WhatsApp user asks
for a human, or on first contact. This backend is that external service, and
must expose the endpoint that stub calls.

Going forward, this backend is where every *other* (future) tenant's direct
Meta WhatsApp integration lives — the dashboard is not the template for new
tenants; per-tenant credential storage here is.

## Goals

- Store and manage per-tenant Meta WhatsApp credentials in this backend.
- Send/receive WhatsApp messages directly via Meta's Graph API, per tenant.
- Verify and process Meta's webhook callbacks (inbound messages, delivery
  status, template status) per tenant, routed by a tenant identifier in the
  webhook URL.
- Provide the call-trigger endpoint the existing dashboard product's stub
  integration needs, so "customer wants a human" on WhatsApp results in an
  actual outbound call or queued call job in this CRM.
- Remove the Superfone-routed WhatsApp code and fix the resulting
  `app/main.py` import.

## Non-Goals

- No AI auto-reply agent for inbound WhatsApp messages in this phase.
  Inbound messages are persisted and visible to human agents via the
  existing `GET /whatsapp/messages` endpoint; nothing replies automatically.
- No changes to the `whatsapp_busness_dashboard` repository. Read-only
  reference only.
- No migration of the dashboard's existing tenant onto this backend's
  pipeline. It keeps running as-is; only the call-trigger endpoint connects
  the two.
- No admin UI. The credential admin surface is a REST API only.

## Architecture

```text
                     Meta Graph API (graph.facebook.com/v21.0)
                       ^                              |
                       | send                         | webhook (per-tenant URL)
                       |                               v
   Tenant N's Meta App |                    FastAPI: app/webhooks/whatsapp/
   (own WABA + number) |                    verifies signature per tenant,
                       |                    persists inbound + status events
                       |
              app/integrations/whatsapp/
              (client.py: stateless Graph API calls
               factory.py: per-tenant client, credentials
               decrypted from whatsapp_tenant_configs)
                       ^
                       |
              app/whatsapp/service.py
              (existing CRM-facing send/list REST API,
               tenant-scoped via RequestContext as today)

   whatsapp_busness_dashboard (separate repo, one tenant, unchanged)
        --POST {phone, reason}, bearer key-->  app/webhooks/whatsapp_dashboard/
                                                (call-trigger endpoint)
                                                        |
                                                        v
                                    app/agent/gateway.py (SFVoPI call)
                                    or app/agent/orchestrator.py (call_job)
```

## Data Model

New migration `029_whatsapp_meta_config.sql`:

```sql
create table public.whatsapp_tenant_configs (
  tenant_id             uuid primary key references public.tenants (id) on delete cascade,
  waba_id               text not null,
  phone_number_id       text not null,
  verify_token          text not null,
  access_token_encrypted text not null,
  app_secret_encrypted  text not null,
  is_active             boolean not null default true,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint uq_whatsapp_tenant_configs_phone_number_id unique (phone_number_id)
);

create trigger trg_whatsapp_tenant_configs_updated_at
  before update on public.whatsapp_tenant_configs
  for each row execute function public.set_updated_at();
```

One config row per tenant (a tenant has exactly one WABA/phone number in
this phase — multiple numbers per tenant is out of scope). `phone_number_id`
is unique across tenants since Meta guarantees it's globally unique, and it
doubles as a sanity check when processing inbound payloads (the value in
the URL path's tenant config should match the payload's
`metadata.phone_number_id` — mismatches are logged but do not block
processing, since the path-derived tenant is the trust boundary, not the
payload).

`access_token_encrypted` / `app_secret_encrypted` are Fernet-encrypted
(`cryptography` package, already a transitive dependency via
`python-jose[cryptography]`) using a new `WHATSAPP_CREDENTIALS_ENCRYPTION_KEY`
setting (a `SecretStr`, base64-encoded 32-byte key, same pattern as other
secrets in `app/core/config.py`). Encryption/decryption happens in the
repository layer; plaintext never appears in logs, responses, or exceptions.

No changes needed to `whatsapp_templates`, `whatsapp_messages`, or
`communication_logs` (migration 008) — those already have `provider default
'meta'` and the exact columns (`wa_id`, `provider_message_id`) this design
needs.

## Components

### `app/integrations/whatsapp/client.py`

Stateless Meta Graph API v21.0 client, ported from the reference
implementation in `whatsapp_busness_dashboard/lib/whatsapp/client.ts`
(request-building + response-parsing, adapted to httpx/async):

- `send_text_message(phone_number_id, access_token, to, body, context_message_id=None)`
- `send_template_message(phone_number_id, access_token, to, template_name, language, components, context_message_id=None)`
- `list_templates(waba_id, access_token, refresh=False)`
- `create_template(waba_id, access_token, name, category, language, components)`
- `get_phone_number_info(phone_number_id, access_token)`

Same failure-handling posture as the (deleted) `SuperfoneWhatsAppClient`:
timeouts and connection errors raise `ExternalServiceError`; a 200 response
is not trusted blindly — a send is only confirmed via
`messages[0].id` being present in the response body; 4xx/5xx map to
`ValidationError` / `NotFoundError` / `ExternalServiceError` as appropriate.
No auto-retry (sending is not idempotent on Meta's side).

### `app/integrations/whatsapp/factory.py`

- `get_client_for_tenant(session, tenant_id) -> tuple[MetaWhatsAppClient-ish call context, phone_number_id, access_token]`
  loads the tenant's `whatsapp_tenant_configs` row, decrypts the access
  token, and returns what callers need to invoke `client.py`'s functions.
  Raises `NotFoundError` (`WHATSAPP_NOT_CONFIGURED`) if the tenant has no
  active config — callers turn this into a clean 4xx, not a 500.

### `app/whatsapp/` (existing module, modified)

- `service.py`: swap the Superfone client dependency for
  `get_client_for_tenant`. `send_message`, `list_templates` behavior is
  otherwise unchanged (still tenant-scoped via `RequestContext`, still
  enforces the 24-hour session-window rule for non-template sends).
- `repository.py`: remove `find_customer_by_phone_cross_tenant` (its cross-
  tenant justification — "single shared platform-wide Superfone
  credential" — no longer holds; tenant is now known from the webhook URL,
  not inferred from an ambiguous phone lookup). Add:
  - `find_customer_by_phone(tenant_id, phone) -> dict | None` — tenant-scoped
    lookup.
  - `create_minimal_customer(tenant_id, phone, full_name) -> dict` — inserts
    a `customers` row with just `tenant_id`, `phone`, `full_name` (Meta's
    contact profile name if the payload includes one, else the phone
    number itself), letting all other columns take their schema defaults.
    Used only when an inbound message arrives from a phone number with no
    existing customer in that tenant.
- `router.py`, `schemas.py`: unchanged.

### `app/webhooks/whatsapp/` (new)

- `router.py`:
  - `GET /api/v1/webhooks/whatsapp/{tenant_id}` — Meta's subscription
    handshake. Looks up `tenant_id`'s config, compares `hub.verify_token`
    to the stored `verify_token` (`hmac.compare_digest`), and if
    `hub.mode == "subscribe"` and it matches, echoes `hub.challenge` with
    `200`. Otherwise `403`.
  - `POST /api/v1/webhooks/whatsapp/{tenant_id}` — looks up `tenant_id`'s
    config (404 if none/inactive — nothing to verify against), verifies
    `x-hub-signature-256` as HMAC-SHA256 over the raw request body against
    the tenant's decrypted `app_secret` (`hmac.compare_digest`, fail
    closed exactly like `verify_whatsapp_webhook_token` does today), then
    parses and dispatches events. Always returns `200` once signature
    verification passes, even if event processing raises — mirrors the
    reference implementation's reasoning: a `5xx` only earns a retry of a
    request that will fail identically, while sustained webhook failures
    cause Meta to disable the subscription outright. Errors are logged,
    not surfaced to Meta.
- `schemas.py`: Pydantic models for the three event shapes Meta sends
  under the `messages` field — inbound message, message status update
  (`sent`/`delivered`/`read`/`failed`) — and for
  `message_template_status_update`.
- `service.py`:
  - `handle_inbound_message`: resolve `customer_id` via
    `find_customer_by_phone` → `create_minimal_customer` on miss → resolve
    `lead_id` if one exists for that customer → `create_message` (direction
    `inbound`, status `delivered`) → `add_communication_log`.
  - `handle_status_update`: `update_message_status_by_provider_id` (already
    implemented, no tenant filter needed since wamids are globally unique)
    → `add_communication_log` if a row was actually updated.
  - `handle_template_status_update`: update `whatsapp_templates.status` /
    `rejection_reason` by `provider_template_id`.
- `security.py`: `verify_meta_signature(raw_body, signature_header,
  app_secret) -> bool` and `verify_meta_verify_token(token, expected) ->
  bool`, both `hmac.compare_digest`-based, no external dependency.

### Call-trigger endpoint — `app/webhooks/whatsapp_dashboard/`

Scoped narrowly to serve the one dashboard-integrated tenant's stub
integration; unrelated to the multi-tenant webhook receiver above.

- `router.py`: `POST /api/v1/webhooks/whatsapp-dashboard/call-agent`.
  Authenticated via a static bearer secret (new
  `WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET` setting), verified the same
  way `verify_superfone_crm_bearer` verifies its bearer header today. Body:
  `{"phone": str, "reason": str}` (matches the dashboard's `triggerCallAgent`
  contract exactly — this backend cannot dictate that shape since it can't
  modify the caller).
- `service.py`:
  - Resolve `phone` to a tenant + customer. Since the caller supplies no
    tenant, this is the one legitimate remaining use for a cross-tenant
    phone lookup — reuses the same "unambiguous single match only, else
    treat as unattributable" logic the deleted
    `find_customer_by_phone_cross_tenant` had, kept here as
    `find_tenant_customer_by_phone_cross_tenant` (moved from
    `app/whatsapp/repository.py`, not duplicated).
  - Resolve a `lead_id` for the matched customer (most recently created
    lead, if one or more exist). `call_jobs.lead_id` is `NOT NULL` and
    `get_next_eligible_jobs` inner-joins `leads`, so a lead is mandatory —
    if the customer has none, auto-create a minimal one (`tenant_id`,
    `customer_id` only; `lead_number` is populated by the existing
    `trg_fn_lead_number` trigger, every other column takes its schema
    default), mirroring the auto-create-customer policy above.
  - If no unambiguous customer match: log and return `200` with
    `{"success": false, "error_code": "CUSTOMER_NOT_FOUND"}` — this is a
    best-effort integration on the caller's side already (its own fetch is
    wrapped in try/catch and ignores the response), so failures here must
    never surface as anything the dashboard needs to handle specially.
  - `reason == "requested"`: place an immediate outbound call using the
    same placement logic `app/agent/gateway.py::_place_sfvopi_call` uses
    today (refactored into a method callable with just
    `tenant_id`/`lead_id`/`customer_id`, not requiring a pre-existing
    `call_job` row — a minimal ad-hoc `call_jobs` row is created first with
    `job_type="whatsapp_callback_request"`, `priority=1` (this system's
    `call_jobs.priority` is an `int` ordered ascending — 1 is the highest
    urgency, below the default of 5) so the existing retry-on-failure
    machinery still applies, then placed immediately instead of waiting in
    the queue).
  - Any other `reason`: `CallOrchestrator.create_call_job(job_type=
    "whatsapp_callback_request", priority=5)` — the same default priority
    used for `initial_lead_call` today — queued, picked up by whatever
    future dispatcher drives `queued -> preparing -> ready -> calling`
    (out of scope here, same gap the README already documents for every
    other call_job today).

### Credential admin API — addition to `app/tenants/`

- `PUT /api/v1/tenants/{tenant_id}/whatsapp-config` — permission-gated by a
  new `Permission.PLATFORM_WHATSAPP_CONFIG_MANAGE` (super-admin only,
  matching the existing `platform.tenant.*` permission naming). Request
  body: `waba_id`, `phone_number_id`, `verify_token`, `access_token`,
  `app_secret` (all plaintext in the request — this is the one time they're
  ever transmitted as plaintext, over HTTPS, by an already-authenticated
  super-admin). Upserts the `whatsapp_tenant_configs` row, encrypting the
  two secrets before storage. Response never includes the secrets.
- `GET /api/v1/tenants/{tenant_id}/whatsapp-config` — same permission.
  Returns `waba_id`, `phone_number_id`, `is_active`, `created_at`,
  `updated_at` — never the encrypted or plaintext secrets.

New `Permission` enum entries in `app/core/permissions.py`:
`PLATFORM_WHATSAPP_CONFIG_MANAGE = "platform.whatsapp_config.manage"`.

## Data Flow — Inbound Message

1. Meta POSTs to `/api/v1/webhooks/whatsapp/{tenant_id}`.
2. Router loads that tenant's config; 404 if missing/inactive.
3. Verifies `x-hub-signature-256` against the tenant's `app_secret`; 401 on
   mismatch, logged, no further processing.
4. Parses the payload into typed events; for each `messages` entry:
   - Inbound message: resolve/create customer, insert `whatsapp_messages`
     row, insert `communication_logs` row.
   - Status callback: update the matching `whatsapp_messages` row by
     `provider_message_id`; if no match, skip (a status event for a
     message never sent through this integration — logged, not an error).
5. Returns `200 EVENT_RECEIVED` regardless of step 4's outcome, once step 3
   passed. Any exception in step 4 is caught, logged with the tenant_id and
   event type, and swallowed.

## Error Handling

- Missing/inactive tenant config → `404` on both webhook and admin-read
  endpoints; never `500`.
- Invalid signature → `401`, logged with tenant_id, no payload processing.
- Meta API errors during send (`list_templates`, `send_message`) → mapped
  `ExternalServiceError`/`ValidationError`/`NotFoundError`, matching the
  existing `app/whatsapp/service.py` error-handling contract — no new
  patterns introduced.
- Ambiguous cross-tenant phone lookup (call-trigger endpoint only) → never
  raises; returns a `success: false` payload, since the caller can't act on
  a thrown error anyway.

## Security

- All three secrets (access token, app secret, per-tenant verify token) are
  encrypted at rest; only `verify_token` is compared in plaintext (it is
  not a cryptographic secret — Meta's own docs treat it as a shared
  low-sensitivity handshake value, same trust level as the existing
  Superfone SFVoPI query-token pattern).
- Signature verification always happens before any DB write from webhook
  input — same fail-closed rule already documented in
  `app/webhooks/superfone/security.py`.
- The credential admin API is the only place plaintext secrets are ever
  accepted, and only by super-admins over an already-authenticated,
  already-encrypted (HTTPS) channel; they are never returned in any
  response.
- `WHATSAPP_CREDENTIALS_ENCRYPTION_KEY` is a new required secret, following
  the same `SecretStr` + env-var pattern as `SUPABASE_JWT_SECRET` etc.

## Cleanup (removed as part of this work)

- `app/integrations/superfone/whatsapp_client.py` — deleted.
- `app/webhooks/superfone/security.py`:
  `verify_whatsapp_webhook_token` — deleted.
- `app/core/config.py`: `SUPERFONE_WHATSAPP_WEBHOOK_SHARED_SECRET` — deleted.
- `app/main.py`: the broken `app.webhooks.superfone.whatsapp.router` import
  and its `include_router` call — deleted (replaced by the new
  `app/webhooks/whatsapp/router.py` and
  `app/webhooks/whatsapp_dashboard/router.py` registrations).
- `app/whatsapp/repository.py`:
  `find_customer_by_phone_cross_tenant` — moved (not duplicated) to the
  call-trigger module as described above.
- `README.md`'s "Not Yet Implemented" section updated to drop the stale
  WhatsApp entry and reflect what's actually built.

## Testing

Matches this repo's existing `tests/` conventions (unit tests colocated by
domain, no live network calls):

- `client.py`: request-building for send/template/list — assert URL, method,
  headers, body shape, given fixed inputs (mirrors the reference repo's own
  `lib/whatsapp/client.test.ts` coverage).
- `security.py`: valid signature accepted; tampered body rejected; missing
  header rejected; wrong `verify_token` rejected on the GET handshake;
  correct one accepted and echoes `hub.challenge`.
- `service.py` (webhook): inbound message with existing customer; inbound
  message with no existing customer (auto-create path); status update for
  a known `provider_message_id`; status update for an unknown one (no-op,
  not an error).
- Call-trigger `service.py`: unambiguous single-tenant phone match; zero
  matches; multiple matches (both treated as unattributable);
  `reason="requested"` triggers immediate placement;
  `reason` anything else queues a call_job.
- Credential admin API: encrypt-on-write, never-returned-on-read, permission
  enforcement (non-super-admin gets `403`).
- Regression: `python -c "import app.main"` succeeds (catches the exact
  failure mode that motivated fixing this).
