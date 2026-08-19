# Multi-Tenant Direct-Meta WhatsApp Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Superfone-routed WhatsApp code (which also fixes a broken import currently preventing the app from starting), and replace it with a multi-tenant direct-Meta WhatsApp Cloud API integration: per-tenant encrypted credential storage, a tenant-scoped webhook receiver, outbound send via the existing `app/whatsapp/` REST API, and a call-trigger endpoint that lets the existing (unmodified) `whatsapp_busness_dashboard` product's WhatsApp AI agent escalate a conversation to an actual outbound call in this CRM.

**Architecture:** Every tenant stores their own Meta WABA credentials (encrypted at rest) in a new `whatsapp_tenant_configs` table. `app/integrations/whatsapp/` holds the stateless Graph API client and a per-tenant factory; `app/webhooks/whatsapp/{tenant_id}` is the inbound webhook receiver, routed and authenticated per tenant via the URL path (never by parsing untrusted payload data first); `app/webhooks/whatsapp_dashboard/` is a narrow, separately-authenticated endpoint serving only the one dashboard-integrated tenant's call-escalation need.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async + raw `text()` SQL (matches this codebase's existing convention — no ORM models), httpx, Pydantic v2, `cryptography` (Fernet), PostgreSQL/Supabase migrations, pytest + pytest-asyncio.

**Spec:** `backend/docs/superpowers/specs/2026-08-19-whatsapp-meta-multitenant-design.md`

## Global Constraints

- No AI auto-reply logic for inbound WhatsApp messages in this phase — messages are persisted only (spec Non-Goals).
- Never touch or re-register the `whatsapp_busness_dashboard` product's Meta webhook subscription; that repository is not modified.
- All secrets (access token, app secret) are Fernet-encrypted at rest via a new `WHATSAPP_CREDENTIALS_ENCRYPTION_KEY` setting; plaintext never appears in logs, responses, or exceptions.
- Signature/token verification always happens before any DB write from webhook input — fail closed, `hmac.compare_digest` only, matching `app/webhooks/superfone/security.py`'s existing pattern.
- Follow this codebase's existing conventions exactly: raw SQL via SQLAlchemy `text()` (no ORM), `app/<domain>/{router,service,repository,schemas}.py` layering, `Permission` enum + `require_permission`/`ensure_tenant_resource_access` for authorization, `AppError` subclasses for all error paths (never bare exceptions), tests colocated in `tests/unit/` mocking `AsyncSession` the way `tests/unit/test_agent_gateway_superfone.py` does (no real DB in tests).
- `call_jobs.priority` is an `int`, ordered ascending (1 = most urgent); `call_jobs.lead_id` is `NOT NULL`.
- Every new/modified Python file must pass `ruff check` and `mypy` (existing CI gates per `README.md`'s Quality Assurance section) — run both before every commit in this plan.

---

## Task 1: Remove Superfone-routed WhatsApp code and fix the app boot failure

This is independently valuable: `app/main.py` currently fails to import (`ModuleNotFoundError: No module named 'app.webhooks.superfone.whatsapp'`), so the app cannot start at all. This task removes the wrong-direction code from last session and restores a working boot, with no dependency on any other task in this plan.

**Files:**
- Delete: `app/integrations/superfone/whatsapp_client.py`
- Modify: `app/integrations/superfone/factory.py`
- Modify: `app/webhooks/superfone/security.py`
- Modify: `app/core/config.py`
- Modify: `app/main.py`

**Interfaces:**
- Produces: nothing new consumed by later tasks — this task only removes code.

- [ ] **Step 1: Confirm today's failure (baseline)**

Run: `cd backend && python -c "import app.main"` (with dummy env vars, since no `.env` exists):

```bash
DATABASE_URL="postgresql+asyncpg://u:p@localhost/db" SUPABASE_URL="https://x.supabase.co" SUPABASE_SERVICE_ROLE_KEY="x" SUPABASE_JWT_SECRET="x" REDIS_URL="redis://localhost" python -c "import app.main"
```

Expected: `ModuleNotFoundError: No module named 'app.webhooks.superfone.whatsapp'`. This confirms the bug this step fixes.

- [ ] **Step 2: Delete the Superfone WhatsApp client**

Delete `app/integrations/superfone/whatsapp_client.py` entirely.

- [ ] **Step 3: Remove its factory function**

In `app/integrations/superfone/factory.py`, remove the import and function:

```python
# DELETE this import:
from app.integrations.superfone.whatsapp_client import SuperfoneWhatsAppClient

# DELETE this function:
def get_superfone_whatsapp_client() -> SuperfoneWhatsAppClient:
    """Build a SuperfoneWhatsAppClient from configured settings.

    Reuses the CRM API credential pair -- see whatsapp_client.py's module
    docstring for the reasoning and the documented uncertainty about it.
    """
    return SuperfoneWhatsAppClient(
        api_key=settings.SUPERFONE_CRM_API_KEY.get_secret_value(),
        base_url=settings.SUPERFONE_CRM_BASE_URL,
    )
```

The file should end with just `get_sfvopi_client` and `get_superfone_crm_client` (both still used for outbound calls — unrelated to WhatsApp).

- [ ] **Step 4: Remove the WhatsApp webhook token verifier**

In `app/webhooks/superfone/security.py`, remove the `verify_whatsapp_webhook_token` function (lines defining it) and update the module docstring's "WhatsApp (Dragonfly)" bullet point to drop the now-false claim that this file verifies it. The docstring should describe only the two streams that remain: SFVoPI and CRM event notifications. Replace the docstring's list with:

```python
"""Webhook authenticity checks for the two Superfone webhook streams this
backend still uses (SFVoPI call callbacks and CRM event notifications).
Superfone documents NO HMAC signature verification for either.

  - SFVoPI (answer/ring/hangup): Superfone cannot be configured to add a
    custom auth header to these particular callbacks (they are set as plain
    URLs on the initiate-call request body: answer_url/ring_url/hangup_url).
    Since we control what URL we register, we embed our own shared-secret
    token as a query parameter and validate it server-side.
  - CRM event notifications: configured entirely via Superfone's dashboard
    automations UI, which DOES support a custom `Authorization: Bearer
    <secret>` header per their own documented recommendation. We validate
    that header server-side.

All checks fail closed: missing/invalid credential -> reject before any DB
write (skills/system.md rule #88).
"""
```

And delete the `verify_whatsapp_webhook_token` function (its whole body, from `def verify_whatsapp_webhook_token` to the line before `def verify_superfone_crm_bearer`).

- [ ] **Step 5: Remove the now-unused config setting**

In `app/core/config.py`, delete the `SUPERFONE_WHATSAPP_WEBHOOK_SHARED_SECRET` field entirely:

```python
# DELETE:
    SUPERFONE_WHATSAPP_WEBHOOK_SHARED_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Shared secret embedded as a query token in the WhatsApp (Dragonfly) "
            "webhook URL we give Superfone to register (there is no self-service "
            "registration API for it, and no signature/auth header exists on this "
            "webhook stream either, per Superfone's own docs) -- same URL-embedded-"
            "token pattern as SUPERFONE_WEBHOOK_SHARED_SECRET, but a separate secret "
            "value so the two webhook streams can be rotated independently."
        ),
    )
```

- [ ] **Step 6: Fix the broken import in `app/main.py`**

In `app/main.py`, remove this import (line 50):

```python
from app.webhooks.superfone.whatsapp.router import router as superfone_whatsapp_webhooks_router
```

And remove its router registration (line 135):

```python
app.include_router(superfone_whatsapp_webhooks_router, prefix=API_V1_PREFIX)
```

Leave `app.include_router(superfone_webhooks_router, prefix=API_V1_PREFIX)` and `app.include_router(whatsapp_router, prefix=API_V1_PREFIX)` in place — both still needed. (The new webhook routers this plan adds are wired in Task 11.)

- [ ] **Step 7: Verify the boot failure is fixed**

Run the same command as Step 1:

```bash
DATABASE_URL="postgresql+asyncpg://u:p@localhost/db" SUPABASE_URL="https://x.supabase.co" SUPABASE_SERVICE_ROLE_KEY="x" SUPABASE_JWT_SECRET="x" REDIS_URL="redis://localhost" python -c "import app.main"
```

Expected: no output, exit code 0 (clean import — no `ModuleNotFoundError`).

- [ ] **Step 8: Run the existing test suite**

Run: `cd backend && pytest -q`

Expected: all tests that passed before still pass (collection itself was failing before this fix, since `tests/conftest.py` imports `app.main` at collection time — so this is also the first point the full suite can run at all this session).

- [ ] **Step 9: Run lint/type checks**

Run: `cd backend && ruff check app && mypy app`

Expected: no new errors introduced by this task's deletions.

- [ ] **Step 10: Commit**

```bash
git add app/integrations/superfone/factory.py app/webhooks/superfone/security.py app/core/config.py app/main.py
git rm app/integrations/superfone/whatsapp_client.py
git commit -m "fix: remove Superfone-routed WhatsApp code, restore app boot"
```

---

## Task 2: Add config settings and the WhatsApp credential permission

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/core/permissions.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY: SecretStr`, `settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET: SecretStr`, `Permission.PLATFORM_WHATSAPP_CONFIG_MANAGE = "platform.whatsapp_config.manage"`.

- [ ] **Step 1: Add the `cryptography` dependency explicitly**

In `pyproject.toml`, in the main `dependencies` list (alongside `"python-jose[cryptography]>=3.3.0"`), add:

```toml
    "cryptography>=42.0.0",
```

It is already present transitively via `python-jose[cryptography]`, but Task 3 imports it directly (`from cryptography.fernet import Fernet`), so it must be a direct dependency, not an implicit one.

Run: `cd backend && pip install -e ".[dev]"` to confirm it resolves cleanly.

- [ ] **Step 2: Add the two new settings**

In `app/core/config.py`, insert immediately after the `SUPERFONE_WHATSAPP_WEBHOOK_SHARED_SECRET` field was removed in Task 1 (i.e., right after `VOICE_AGENT_STREAM_SAMPLE_RATE`, before `TRUST_PROXY_HEADERS`):

```python
    WHATSAPP_CREDENTIALS_ENCRYPTION_KEY: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Base64-encoded 32-byte Fernet key used to encrypt each tenant's "
            "Meta WhatsApp access_token and app_secret at rest in "
            "whatsapp_tenant_configs. Generate with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`. Required before any "
            "tenant WhatsApp credentials can be stored or used -- "
            "app/integrations/whatsapp/crypto.py fails closed if empty."
        ),
    )
    WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Expected value of the Authorization: Bearer header on the "
            "whatsapp_busness_dashboard product's call-agent trigger "
            "requests (POST /webhooks/whatsapp-dashboard/call-agent). That "
            "product is a separate, unmodified repository -- this secret is "
            "configured as its CALL_AGENT_API_KEY environment variable."
        ),
    )
```

- [ ] **Step 3: Add the new permission**

In `app/core/permissions.py`, in the `Permission` enum, add to the "Platform" section (after `PLATFORM_ANALYTICS_READ`):

```python
    PLATFORM_WHATSAPP_CONFIG_MANAGE = "platform.whatsapp_config.manage"
```

- [ ] **Step 4: Run lint/type checks**

Run: `cd backend && ruff check app && mypy app`

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py app/core/permissions.py pyproject.toml
git commit -m "feat: add WhatsApp credential encryption/bearer settings and admin permission"
```

---

## Task 3: Migration 029 — `whatsapp_tenant_configs` table, RLS, and permission seed

**Files:**
- Create: `supabase/migrations/029_whatsapp_meta_config.sql`

**Interfaces:**
- Produces: table `public.whatsapp_tenant_configs (tenant_id, waba_id, phone_number_id, verify_token, access_token_encrypted, app_secret_encrypted, is_active, created_at, updated_at)`; DB permission `platform.whatsapp_config.manage` granted to `super_admin`.

- [ ] **Step 1: Write the migration**

Create `supabase/migrations/029_whatsapp_meta_config.sql`:

```sql
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
```

- [ ] **Step 2: Verify migration syntax**

Run (if a local/test Postgres/Supabase instance is available per this repo's normal migration workflow — see `backend/README.md`'s Docker Support section for `docker compose up`):

```bash
docker compose up -d postgres
psql "$DATABASE_URL" -f supabase/migrations/029_whatsapp_meta_config.sql
```

Expected: no SQL errors; `\d public.whatsapp_tenant_configs` shows the new table.

If no local Postgres is available in this environment, at minimum verify the file parses as valid SQL syntax by eye against migrations 008 and 018 (both referenced above) — the CREATE TABLE, trigger, RLS, and permission-seed blocks each mirror an existing, already-applied migration's exact shape.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/029_whatsapp_meta_config.sql
git commit -m "feat: add whatsapp_tenant_configs table and admin permission (migration 029)"
```

---

## Task 4: Credential encryption utility and tenant config repository

**Files:**
- Create: `app/integrations/whatsapp/__init__.py`
- Create: `app/integrations/whatsapp/crypto.py`
- Create: `app/integrations/whatsapp/repository.py`
- Test: `tests/unit/test_whatsapp_crypto.py`
- Test: `tests/unit/test_whatsapp_tenant_config_repository.py`

**Interfaces:**
- Consumes: `app.core.config.settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY` (Task 2).
- Produces: `encrypt_secret(plaintext: str) -> str`, `decrypt_secret(ciphertext: str) -> str` (both in `crypto.py`); `class WhatsAppTenantConfigRepository` with `async def upsert(tenant_id, waba_id, phone_number_id, verify_token, access_token_plain, app_secret_plain) -> dict`, `async def get_public(tenant_id) -> dict | None`, `async def get_decrypted(tenant_id) -> dict | None` (the latter returns `access_token` and `app_secret` decrypted, plus all other columns) — used by Task 5's client factory, Task 6's admin API, and Task 8's webhook receiver.

- [ ] **Step 1: Write the failing crypto test**

Create `tests/unit/test_whatsapp_crypto.py`:

```python
"""Unit tests for Fernet-based encryption of WhatsApp tenant credentials."""

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.core.exceptions import BusinessRuleError
from app.integrations.whatsapp.crypto import decrypt_secret, encrypt_secret


class _FakeSecretStr:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def _valid_key() -> str:
    return Fernet.generate_key().decode()


def test_encrypt_then_decrypt_roundtrips() -> None:
    """Verify a value encrypted with encrypt_secret decrypts back to the
    original plaintext with decrypt_secret."""
    with patch("app.integrations.whatsapp.crypto.settings") as mock_settings:
        mock_settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY = _FakeSecretStr(_valid_key())
        ciphertext = encrypt_secret("my-access-token")
        assert ciphertext != "my-access-token"
        assert decrypt_secret(ciphertext) == "my-access-token"


def test_encrypt_fails_closed_when_key_unconfigured() -> None:
    """Verify encryption refuses to run with an empty encryption key rather
    than silently storing plaintext or crashing with an obscure error."""
    with patch("app.integrations.whatsapp.crypto.settings") as mock_settings:
        mock_settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY = _FakeSecretStr("")
        with pytest.raises(BusinessRuleError) as exc_info:
            encrypt_secret("my-access-token")
    assert exc_info.value.code == "WHATSAPP_ENCRYPTION_KEY_NOT_CONFIGURED"


def test_decrypt_fails_closed_when_key_unconfigured() -> None:
    """Same fail-closed behavior on the decrypt path."""
    with patch("app.integrations.whatsapp.crypto.settings") as mock_settings:
        mock_settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY = _FakeSecretStr("")
        with pytest.raises(BusinessRuleError) as exc_info:
            decrypt_secret("anything")
    assert exc_info.value.code == "WHATSAPP_ENCRYPTION_KEY_NOT_CONFIGURED"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.integrations.whatsapp.crypto'`.

- [ ] **Step 3: Create the package and implement `crypto.py`**

Create `app/integrations/whatsapp/__init__.py` (empty file, matching every other domain package's `__init__.py` in this repo).

Create `app/integrations/whatsapp/crypto.py`:

```python
"""Fernet-based at-rest encryption for per-tenant Meta WhatsApp credentials
(access_token, app_secret) stored in whatsapp_tenant_configs.

Fails closed: an unconfigured or malformed WHATSAPP_CREDENTIALS_ENCRYPTION_KEY
raises rather than silently storing plaintext or producing an unreadable
cryptography-library stack trace at the call site.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import BusinessRuleError


def _get_fernet() -> Fernet:
    key = settings.WHATSAPP_CREDENTIALS_ENCRYPTION_KEY.get_secret_value()
    if not key:
        raise BusinessRuleError(
            message=(
                "WhatsApp credential encryption is not configured "
                "(WHATSAPP_CREDENTIALS_ENCRYPTION_KEY is empty)."
            ),
            code="WHATSAPP_ENCRYPTION_KEY_NOT_CONFIGURED",
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise BusinessRuleError(
            message="WHATSAPP_CREDENTIALS_ENCRYPTION_KEY is not a valid Fernet key.",
            code="WHATSAPP_ENCRYPTION_KEY_INVALID",
        ) from e


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret for storage. Returns a Fernet token (str)."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored Fernet token back to plaintext."""
    fernet = _get_fernet()
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise BusinessRuleError(
            message="Stored WhatsApp credential could not be decrypted.",
            code="WHATSAPP_CREDENTIAL_DECRYPT_FAILED",
        ) from e
```

- [ ] **Step 4: Run crypto test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_crypto.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing repository test**

Create `tests/unit/test_whatsapp_tenant_config_repository.py`:

```python
"""Unit tests for WhatsAppTenantConfigRepository (encrypt-on-write,
never-decrypt-in-the-public-read-path)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.integrations.whatsapp.repository import WhatsAppTenantConfigRepository


@pytest.mark.asyncio
async def test_upsert_encrypts_secrets_before_insert() -> None:
    """Verify the raw SQL params passed to session.execute carry ciphertext,
    never the plaintext access_token/app_secret arguments."""
    mock_session = AsyncMock()
    upsert_res = MagicMock()
    upsert_res.mappings.return_value.one.return_value = {
        "tenant_id": uuid4(),
        "waba_id": "waba-1",
        "phone_number_id": "phone-1",
        "is_active": True,
    }
    mock_session.execute.return_value = upsert_res
    repo = WhatsAppTenantConfigRepository(mock_session)
    tenant_id = uuid4()

    with (
        patch(
            "app.integrations.whatsapp.repository.encrypt_secret",
            side_effect=lambda v: f"encrypted:{v}",
        ) as mock_encrypt,
    ):
        await repo.upsert(
            tenant_id=tenant_id,
            waba_id="waba-1",
            phone_number_id="phone-1",
            verify_token="verify-1",
            access_token_plain="plain-access-token",
            app_secret_plain="plain-app-secret",
        )

    mock_encrypt.assert_any_call("plain-access-token")
    mock_encrypt.assert_any_call("plain-app-secret")
    params = mock_session.execute.call_args.args[1]
    assert params["access_token_encrypted"] == "encrypted:plain-access-token"
    assert params["app_secret_encrypted"] == "encrypted:plain-app-secret"
    assert "plain-access-token" not in params.values()


@pytest.mark.asyncio
async def test_get_public_never_returns_secret_columns() -> None:
    """Verify get_public's SELECT does not fetch the encrypted columns at
    all -- not just that the response omits them."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = {
        "tenant_id": uuid4(),
        "waba_id": "waba-1",
        "phone_number_id": "phone-1",
        "is_active": True,
    }
    mock_session.execute.return_value = select_res
    repo = WhatsAppTenantConfigRepository(mock_session)

    result = await repo.get_public(uuid4())

    query_text = str(mock_session.execute.call_args.args[0])
    assert "access_token_encrypted" not in query_text
    assert "app_secret_encrypted" not in query_text
    assert result is not None
    assert "waba_id" in result


@pytest.mark.asyncio
async def test_get_decrypted_returns_plaintext_fields() -> None:
    """Verify get_decrypted decrypts both secret columns into plaintext
    access_token/app_secret keys for internal callers (client factory,
    webhook receiver)."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = {
        "tenant_id": uuid4(),
        "waba_id": "waba-1",
        "phone_number_id": "phone-1",
        "verify_token": "verify-1",
        "access_token_encrypted": "cipher-access",
        "app_secret_encrypted": "cipher-secret",
        "is_active": True,
    }
    mock_session.execute.return_value = select_res
    repo = WhatsAppTenantConfigRepository(mock_session)

    with patch(
        "app.integrations.whatsapp.repository.decrypt_secret",
        side_effect=lambda v: v.replace("cipher", "plain"),
    ):
        result = await repo.get_decrypted(uuid4())

    assert result is not None
    assert result["access_token"] == "plain-access"
    assert result["app_secret"] == "plain-secret"
    assert "access_token_encrypted" not in result
    assert "app_secret_encrypted" not in result


@pytest.mark.asyncio
async def test_get_decrypted_returns_none_when_no_config() -> None:
    """Verify a tenant with no config row returns None, not an error --
    callers (factory, webhook router) turn this into a clean 404."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = None
    mock_session.execute.return_value = select_res
    repo = WhatsAppTenantConfigRepository(mock_session)

    result = await repo.get_decrypted(uuid4())

    assert result is None
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_tenant_config_repository.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 7: Implement `repository.py`**

Create `app/integrations/whatsapp/repository.py`:

```python
"""Repository for per-tenant Meta WhatsApp credentials
(whatsapp_tenant_configs). Encrypts on write, decrypts only in
get_decrypted -- get_public never touches the encrypted columns at all."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.whatsapp.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


class WhatsAppTenantConfigRepository:
    """Repository handling database access for whatsapp_tenant_configs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        waba_id: str,
        phone_number_id: str,
        verify_token: str,
        access_token_plain: str,
        app_secret_plain: str,
    ) -> dict[str, Any]:
        """Create or replace a tenant's WhatsApp config, encrypting both
        secrets before they ever reach the database."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.whatsapp_tenant_configs (
                    tenant_id, waba_id, phone_number_id, verify_token,
                    access_token_encrypted, app_secret_encrypted
                ) VALUES (
                    :tenant_id, :waba_id, :phone_number_id, :verify_token,
                    :access_token_encrypted, :app_secret_encrypted
                )
                ON CONFLICT (tenant_id) DO UPDATE SET
                    waba_id = EXCLUDED.waba_id,
                    phone_number_id = EXCLUDED.phone_number_id,
                    verify_token = EXCLUDED.verify_token,
                    access_token_encrypted = EXCLUDED.access_token_encrypted,
                    app_secret_encrypted = EXCLUDED.app_secret_encrypted,
                    is_active = true,
                    updated_at = NOW()
                RETURNING tenant_id, waba_id, phone_number_id, is_active,
                          created_at, updated_at
                """
            ),
            {
                "tenant_id": tenant_id,
                "waba_id": waba_id,
                "phone_number_id": phone_number_id,
                "verify_token": verify_token,
                "access_token_encrypted": encrypt_secret(access_token_plain),
                "app_secret_encrypted": encrypt_secret(app_secret_plain),
            },
        )
        return dict(result.mappings().one())

    async def get_public(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Fetch a tenant's WhatsApp config metadata -- never the secret
        columns, not even encrypted, since the admin GET endpoint must never
        return them."""
        result = await self.session.execute(
            text(
                """
                SELECT tenant_id, waba_id, phone_number_id, is_active,
                       created_at, updated_at
                FROM public.whatsapp_tenant_configs
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_decrypted(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Fetch a tenant's full WhatsApp config with both secrets
        decrypted to plaintext, for internal use only (client factory,
        webhook signature verification). Returns None if the tenant has no
        config row -- callers turn this into a 404, not a 500."""
        result = await self.session.execute(
            text(
                """
                SELECT tenant_id, waba_id, phone_number_id, verify_token,
                       access_token_encrypted, app_secret_encrypted, is_active
                FROM public.whatsapp_tenant_configs
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        if not row:
            return None
        data = dict(row)
        data["access_token"] = decrypt_secret(data.pop("access_token_encrypted"))
        data["app_secret"] = decrypt_secret(data.pop("app_secret_encrypted"))
        return data
```

- [ ] **Step 8: Run repository test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_tenant_config_repository.py -v`
Expected: PASS (4 tests).

- [ ] **Step 9: Run lint/type checks**

Run: `cd backend && ruff check app tests && mypy app`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add app/integrations/whatsapp/__init__.py app/integrations/whatsapp/crypto.py app/integrations/whatsapp/repository.py tests/unit/test_whatsapp_crypto.py tests/unit/test_whatsapp_tenant_config_repository.py
git commit -m "feat: add WhatsApp credential encryption and tenant config repository"
```

---

## Task 5: Meta Graph API client and per-tenant factory

**Files:**
- Create: `app/integrations/whatsapp/client.py`
- Create: `app/integrations/whatsapp/factory.py`
- Test: `tests/unit/test_whatsapp_meta_client.py`
- Test: `tests/unit/test_whatsapp_client_factory.py`

**Interfaces:**
- Consumes: `WhatsAppTenantConfigRepository.get_decrypted` (Task 4).
- Produces: `class MetaWhatsAppClient` with `async def send_text_message(*, to, body, context_message_id=None)`, `async def send_message(*, to, message_type, message, context_message_id=None)`, `async def send_template_message(*, to, template_name, language, components, context_message_id=None)`, `async def list_templates(refresh=False)`; `async def get_client_for_tenant(session, tenant_id) -> MetaWhatsAppClient` (raises `NotFoundError` code `WHATSAPP_NOT_CONFIGURED` if no active config) — consumed by Task 7 (`app/whatsapp/service.py`).

- [ ] **Step 1: Write the failing client test**

Create `tests/unit/test_whatsapp_meta_client.py`:

```python
"""Unit tests for MetaWhatsAppClient's request-building and response
parsing. Ported from whatsapp_busness_dashboard's lib/whatsapp/client.ts
reference implementation (read-only reference, no code shared)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.integrations.whatsapp.client import MetaWhatsAppClient


def _client() -> MetaWhatsAppClient:
    return MetaWhatsAppClient(
        phone_number_id="770971286099252",
        access_token="test-access-token",
        waba_id="789424670144149",
    )


@pytest.mark.asyncio
async def test_send_text_message_builds_correct_request() -> None:
    """Verify the request hits graph.facebook.com/v21.0/{phone_number_id}/messages
    with a Bearer auth header and Meta's documented text-message body shape."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"messages": [{"id": "wamid.ABC123"}]}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
        result = await _client().send_text_message(to="+919999999999", body="hello")

    assert result == {"message_id": "wamid.ABC123"}
    call_kwargs = mock_post.call_args.kwargs
    assert mock_post.call_args.args[0] == (
        "https://graph.facebook.com/v21.0/770971286099252/messages"
    )
    assert call_kwargs["headers"]["Authorization"] == "Bearer test-access-token"
    assert call_kwargs["json"]["to"] == "919999999999"
    assert call_kwargs["json"]["type"] == "text"
    assert call_kwargs["json"]["text"] == {"body": "hello"}


@pytest.mark.asyncio
async def test_send_template_message_builds_correct_request() -> None:
    """Verify template sends carry Meta's template payload shape."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"messages": [{"id": "wamid.DEF456"}]}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
        result = await _client().send_template_message(
            to="+919999999999",
            template_name="site_visit_reminder",
            language="hi",
            components=[{"type": "body", "parameters": []}],
        )

    assert result == {"message_id": "wamid.DEF456"}
    body = mock_post.call_args.kwargs["json"]
    assert body["type"] == "template"
    assert body["template"]["name"] == "site_visit_reminder"
    assert body["template"]["language"] == {"code": "hi"}
    assert body["template"]["components"] == [{"type": "body", "parameters": []}]


@pytest.mark.asyncio
async def test_send_message_raises_when_no_message_id_confirmed() -> None:
    """Verify a 200 response with no messages[0].id is treated as an
    unconfirmed send, not a silent success -- mirrors the reference
    implementation's parseSendMessageResponse contract."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"error": {"message": "rejected"}}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(ExternalServiceError) as exc_info:
            await _client().send_text_message(to="+919999999999", body="hello")

    assert exc_info.value.code == "WHATSAPP_SEND_NOT_CONFIRMED"


@pytest.mark.asyncio
async def test_send_message_raises_on_timeout() -> None:
    """Verify a timeout maps to a clean ExternalServiceError, not a raw
    httpx exception leaking out of this integration boundary."""
    with patch(
        "httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    ):
        with pytest.raises(ExternalServiceError) as exc_info:
            await _client().send_text_message(to="+919999999999", body="hello")

    assert exc_info.value.code == "WHATSAPP_TIMEOUT"


@pytest.mark.asyncio
async def test_list_templates_parses_data_array() -> None:
    """Verify list_templates hits the WABA's message_templates endpoint and
    returns the `data` array from Meta's response envelope."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": [{"name": "greeting", "status": "APPROVED"}]}

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)) as mock_get:
        result = await _client().list_templates()

    assert result == [{"name": "greeting", "status": "APPROVED"}]
    assert mock_get.call_args.args[0] == (
        "https://graph.facebook.com/v21.0/789424670144149/message_templates"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_meta_client.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `client.py`**

Create `app/integrations/whatsapp/client.py`:

```python
"""Meta WhatsApp Cloud API client (Graph API v21.0), per-tenant.

Ported from the reference implementation in the (read-only, unmodified)
whatsapp_busness_dashboard repository's lib/whatsapp/client.ts -- same
request shapes, same "a 200 response is not automatically a confirmed
send" parsing rule, adapted to httpx/async Python. No auto-retry: sending
a WhatsApp message is not idempotent on Meta's side.
"""

import logging
from typing import Any, Literal

import httpx

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

WhatsAppMessageType = Literal[
    "template", "text", "image", "video", "audio", "document", "location", "interactive",
    "contacts", "reaction",
]


def _to_recipient(phone: str) -> str:
    """Meta's `to` field is country-code-prefixed with NO leading `+`, while
    this codebase's customers.phone is stored as normalized E.164 WITH a
    leading `+`. Strip it here at the integration boundary."""
    return phone.lstrip("+")


class MetaWhatsAppClient:
    """Per-tenant Meta WhatsApp Cloud API client. One instance per request,
    built by app.integrations.whatsapp.factory.get_client_for_tenant --
    never constructed directly outside tests."""

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        waba_id: str,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._waba_id = waba_id
        self._timeout = timeout or _DEFAULT_TIMEOUT

    async def send_text_message(
        self, *, to: str, body: str, context_message_id: str | None = None
    ) -> dict[str, Any]:
        """Send a free-form text message. ONLY works within 24 hours of the
        customer's last inbound message -- callers must check that window
        themselves (see app/whatsapp/service.py) BEFORE calling this."""
        return await self.send_message(
            to=to, message_type="text", message={"body": body}, context_message_id=context_message_id
        )

    async def send_message(
        self,
        *,
        to: str,
        message_type: WhatsAppMessageType,
        message: dict[str, Any],
        context_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send any non-template message type."""
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": _to_recipient(to),
            "type": message_type,
            message_type: message,
        }
        if context_message_id:
            body["context"] = {"message_id": context_message_id}
        return await self._send(body)

    async def send_template_message(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        components: list[dict[str, Any]],
        context_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a template message. Works anytime -- approved templates are
        not subject to the 24-hour session-window rule."""
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": _to_recipient(to),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components,
            },
        }
        if context_message_id:
            body["context"] = {"message_id": context_message_id}
        return await self._send(body)

    async def list_templates(self, refresh: bool = False) -> list[dict[str, Any]]:
        """GET .../{waba_id}/message_templates -- list approved/pending/
        rejected templates."""
        url = f"{_GRAPH_API_BASE}/{self._waba_id}/message_templates"
        params = {"fields": "name,status,category,language,components"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http_client:
                response = await http_client.get(
                    url, params=params, headers={"Authorization": f"Bearer {self._access_token}"}
                )
        except httpx.TimeoutException as e:
            raise ExternalServiceError(
                message="Meta WhatsApp API did not respond in time.", code="WHATSAPP_TIMEOUT"
            ) from e
        except httpx.HTTPError as e:
            raise ExternalServiceError(
                message="Could not reach Meta WhatsApp API.", code="WHATSAPP_UNREACHABLE"
            ) from e

        if response.status_code == 200:
            try:
                return list(response.json()["data"])
            except (ValueError, KeyError, TypeError) as e:
                raise ExternalServiceError(
                    message="Meta WhatsApp API returned an unexpected response shape.",
                    code="WHATSAPP_MALFORMED_RESPONSE",
                ) from e

        self._raise_for_error_status(response)
        raise ExternalServiceError(
            message=f"Meta WhatsApp API returned an unexpected status ({response.status_code}).",
            code="WHATSAPP_UNEXPECTED_RESPONSE",
        )

    async def _send(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{_GRAPH_API_BASE}/{self._phone_number_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http_client:
                response = await http_client.post(
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as e:
            raise ExternalServiceError(
                message="Meta WhatsApp API did not respond in time.", code="WHATSAPP_TIMEOUT"
            ) from e
        except httpx.HTTPError as e:
            raise ExternalServiceError(
                message="Could not reach Meta WhatsApp API.", code="WHATSAPP_UNREACHABLE"
            ) from e
        return self._parse_send_response(response)

    def _parse_send_response(self, response: httpx.Response) -> dict[str, Any]:
        """A 200 status does NOT guarantee the message was sent -- the only
        real success signal is messages[0].id being present."""
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as e:
                raise ExternalServiceError(
                    message="Meta WhatsApp API returned an unparseable response.",
                    code="WHATSAPP_MALFORMED_RESPONSE",
                ) from e
            try:
                message_id = payload["messages"][0]["id"]
                if not message_id:
                    raise KeyError("messages[0].id empty")
                return {"message_id": message_id}
            except (KeyError, IndexError, TypeError) as e:
                logger.error(f"WhatsApp send returned 200 but no confirmed message id: {response.text!r}")
                raise ExternalServiceError(
                    message="Meta WhatsApp API returned HTTP 200 but did not confirm the message was sent.",
                    code="WHATSAPP_SEND_NOT_CONFIRMED",
                ) from e

        self._raise_for_error_status(response)
        raise ExternalServiceError(
            message=f"Meta WhatsApp API returned an unexpected status ({response.status_code}).",
            code="WHATSAPP_UNEXPECTED_RESPONSE",
        )

    def _raise_for_error_status(self, response: httpx.Response) -> None:
        if response.status_code == 400:
            detail = _safe_error_message(response)
            raise ValidationError(
                message=f"Meta rejected the WhatsApp request: {detail}",
                code="WHATSAPP_INVALID_REQUEST",
            )
        if response.status_code in (401, 403):
            raise ExternalServiceError(
                message="Meta WhatsApp API rejected our credentials.", code="WHATSAPP_UNAUTHORIZED"
            )
        if response.status_code == 404:
            raise NotFoundError(
                message="Meta WhatsApp resource not found (check phone_number_id/waba_id).",
                code="WHATSAPP_RESOURCE_NOT_FOUND",
            )
        if response.status_code >= 500:
            raise ExternalServiceError(
                message="Meta WhatsApp API failed to process the request.",
                code="WHATSAPP_PROVIDER_ERROR",
            )


def _safe_error_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", response.text))
    except ValueError:
        return response.text
```

- [ ] **Step 4: Run client test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_meta_client.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Write the failing factory test**

Create `tests/unit/test_whatsapp_client_factory.py`:

```python
"""Unit tests for the per-tenant Meta WhatsApp client factory."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundError
from app.integrations.whatsapp.factory import get_client_for_tenant


@pytest.mark.asyncio
async def test_get_client_for_tenant_builds_client_from_config() -> None:
    """Verify the factory loads the tenant's decrypted config and builds a
    MetaWhatsAppClient wired to it."""
    mock_session = AsyncMock()
    tenant_id = uuid4()

    with patch(
        "app.integrations.whatsapp.factory.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={
                "tenant_id": tenant_id,
                "waba_id": "waba-1",
                "phone_number_id": "phone-1",
                "access_token": "plain-token",
                "app_secret": "plain-secret",
                "is_active": True,
            }
        )
        client = await get_client_for_tenant(mock_session, tenant_id)

    assert client._phone_number_id == "phone-1"
    assert client._access_token == "plain-token"
    assert client._waba_id == "waba-1"


@pytest.mark.asyncio
async def test_get_client_for_tenant_raises_when_not_configured() -> None:
    """Verify a tenant with no WhatsApp config raises a clean 404, not an
    AttributeError from a None config."""
    mock_session = AsyncMock()

    with patch(
        "app.integrations.whatsapp.factory.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError) as exc_info:
            await get_client_for_tenant(mock_session, uuid4())

    assert exc_info.value.code == "WHATSAPP_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_get_client_for_tenant_raises_when_inactive() -> None:
    """Verify an explicitly deactivated config is treated the same as no
    config at all -- is_active is the intended kill switch for rotation."""
    mock_session = AsyncMock()

    with patch(
        "app.integrations.whatsapp.factory.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={
                "tenant_id": uuid4(),
                "waba_id": "waba-1",
                "phone_number_id": "phone-1",
                "access_token": "plain-token",
                "app_secret": "plain-secret",
                "is_active": False,
            }
        )
        with pytest.raises(NotFoundError) as exc_info:
            await get_client_for_tenant(mock_session, uuid4())

    assert exc_info.value.code == "WHATSAPP_NOT_CONFIGURED"
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_client_factory.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 7: Implement `factory.py`**

Create `app/integrations/whatsapp/factory.py`:

```python
"""Factory building a per-tenant MetaWhatsAppClient from that tenant's
stored (encrypted) credentials. Keeps DB lookups and decryption out of
callers (app/whatsapp/service.py, app/webhooks/whatsapp/service.py)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.integrations.whatsapp.client import MetaWhatsAppClient
from app.integrations.whatsapp.repository import WhatsAppTenantConfigRepository


async def get_client_for_tenant(session: AsyncSession, tenant_id: UUID) -> MetaWhatsAppClient:
    """Build a MetaWhatsAppClient for the given tenant. Raises NotFoundError
    (WHATSAPP_NOT_CONFIGURED) if the tenant has no active config, so callers
    can turn this into a clean 404 rather than a 500."""
    repo = WhatsAppTenantConfigRepository(session)
    config = await repo.get_decrypted(tenant_id)
    if not config or not config["is_active"]:
        raise NotFoundError(
            message=f"Tenant '{tenant_id}' has no active WhatsApp configuration.",
            code="WHATSAPP_NOT_CONFIGURED",
        )
    return MetaWhatsAppClient(
        phone_number_id=config["phone_number_id"],
        access_token=config["access_token"],
        waba_id=config["waba_id"],
    )
```

- [ ] **Step 8: Run factory test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_client_factory.py -v`
Expected: PASS (3 tests).

- [ ] **Step 9: Run lint/type checks**

Run: `cd backend && ruff check app tests && mypy app`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add app/integrations/whatsapp/client.py app/integrations/whatsapp/factory.py tests/unit/test_whatsapp_meta_client.py tests/unit/test_whatsapp_client_factory.py
git commit -m "feat: add Meta WhatsApp Cloud API client and per-tenant factory"
```

---

## Task 6: Credential admin API on `app/tenants/`

**Files:**
- Modify: `app/tenants/schemas.py`
- Modify: `app/tenants/service.py`
- Modify: `app/tenants/router.py`
- Test: `tests/unit/test_tenants_whatsapp_config_service.py`

**Interfaces:**
- Consumes: `WhatsAppTenantConfigRepository` (Task 4).
- Produces: `PUT /api/v1/tenants/{tenant_id}/whatsapp-config`, `GET /api/v1/tenants/{tenant_id}/whatsapp-config`, both gated by `Permission.PLATFORM_WHATSAPP_CONFIG_MANAGE` (Task 2).

- [ ] **Step 1: Write the failing service test**

Create `tests/unit/test_tenants_whatsapp_config_service.py`:

```python
"""Unit tests for TenantService's WhatsApp credential admin methods."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.tenants.schemas import WhatsAppConfigUpsertRequest
from app.tenants.service import TenantService


@pytest.mark.asyncio
async def test_upsert_whatsapp_config_delegates_to_repository() -> None:
    """Verify the service passes plaintext secrets straight through to the
    repository (which encrypts them) and returns a response with no secret
    fields at all."""
    mock_session = AsyncMock()
    tenant_id = uuid4()
    service = TenantService(mock_session)

    with patch(
        "app.tenants.service.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.upsert = AsyncMock(
            return_value={
                "tenant_id": tenant_id,
                "waba_id": "waba-1",
                "phone_number_id": "phone-1",
                "is_active": True,
                "created_at": "2026-08-19T00:00:00Z",
                "updated_at": "2026-08-19T00:00:00Z",
            }
        )
        result = await service.upsert_whatsapp_config(
            tenant_id,
            WhatsAppConfigUpsertRequest(
                waba_id="waba-1",
                phone_number_id="phone-1",
                verify_token="verify-1",
                access_token="plain-token",
                app_secret="plain-secret",
            ),
        )

    mock_repo_cls.return_value.upsert.assert_awaited_once_with(
        tenant_id=tenant_id,
        waba_id="waba-1",
        phone_number_id="phone-1",
        verify_token="verify-1",
        access_token_plain="plain-token",
        app_secret_plain="plain-secret",
    )
    assert not hasattr(result, "access_token")
    assert not hasattr(result, "app_secret")
    assert result.waba_id == "waba-1"


@pytest.mark.asyncio
async def test_get_whatsapp_config_raises_not_found_when_unconfigured() -> None:
    """Verify GET returns a clean 404 for a tenant with no config yet."""
    from app.core.exceptions import NotFoundError

    mock_session = AsyncMock()
    service = TenantService(mock_session)

    with patch(
        "app.tenants.service.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_public = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError):
            await service.get_whatsapp_config(uuid4())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_tenants_whatsapp_config_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'WhatsAppConfigUpsertRequest'`.

- [ ] **Step 3: Add the two schemas**

In `app/tenants/schemas.py`, append:

```python
class WhatsAppConfigUpsertRequest(BaseModel):
    """Request payload to create/rotate a tenant's Meta WhatsApp
    credentials. All fields are plaintext here -- the only time these
    secrets are ever transmitted as plaintext, over HTTPS, by an
    already-authenticated super-admin. Encrypted at rest by
    WhatsAppTenantConfigRepository.upsert."""

    waba_id: str = Field(..., min_length=1)
    phone_number_id: str = Field(..., min_length=1)
    verify_token: str = Field(..., min_length=1)
    access_token: str = Field(..., min_length=1)
    app_secret: str = Field(..., min_length=1)


class WhatsAppConfigResponse(BaseModel):
    """Tenant WhatsApp config metadata -- deliberately excludes
    verify_token, access_token, and app_secret. Never add them here."""

    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    waba_id: str
    phone_number_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Add the two service methods**

In `app/tenants/service.py`, add the import and methods:

```python
from app.core.exceptions import NotFoundError, ValidationError
from app.integrations.whatsapp.repository import WhatsAppTenantConfigRepository
from app.tenants.schemas import (
    TenantResponse,
    TenantUpdate,
    WhatsAppConfigResponse,
    WhatsAppConfigUpsertRequest,
)
```

(merge with the existing import lines rather than duplicating), and add to `TenantService`:

```python
    async def upsert_whatsapp_config(
        self, tenant_id: UUID, data: WhatsAppConfigUpsertRequest
    ) -> WhatsAppConfigResponse:
        """Create or rotate a tenant's Meta WhatsApp credentials."""
        existing = await self.repository.get_by_id(tenant_id)
        if not existing:
            raise NotFoundError(
                message=f"Tenant with ID '{tenant_id}' was not found.", code="TENANT_NOT_FOUND"
            )
        whatsapp_repo = WhatsAppTenantConfigRepository(self.repository.session)
        row = await whatsapp_repo.upsert(
            tenant_id=tenant_id,
            waba_id=data.waba_id,
            phone_number_id=data.phone_number_id,
            verify_token=data.verify_token,
            access_token_plain=data.access_token,
            app_secret_plain=data.app_secret,
        )
        return WhatsAppConfigResponse.model_validate(row)

    async def get_whatsapp_config(self, tenant_id: UUID) -> WhatsAppConfigResponse:
        """Fetch a tenant's WhatsApp config metadata (never the secrets)."""
        whatsapp_repo = WhatsAppTenantConfigRepository(self.repository.session)
        row = await whatsapp_repo.get_public(tenant_id)
        if not row:
            raise NotFoundError(
                message=f"Tenant '{tenant_id}' has no WhatsApp configuration.",
                code="WHATSAPP_NOT_CONFIGURED",
            )
        return WhatsAppConfigResponse.model_validate(row)
```

`TenantRepository` does not currently expose its `session` attribute publicly by name elsewhere, but it is a plain instance attribute (`self.session = session` in `__init__`), so `self.repository.session` is valid.

- [ ] **Step 5: Run service test to verify it passes**

Run: `cd backend && pytest tests/unit/test_tenants_whatsapp_config_service.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Add the two router endpoints**

In `app/tenants/router.py`, add the import and endpoints:

```python
from app.tenants.schemas import (
    TenantResponse,
    TenantUpdate,
    WhatsAppConfigResponse,
    WhatsAppConfigUpsertRequest,
)
```

(merge with existing import), and append to the router:

```python
@router.put(
    "/{tenant_id}/whatsapp-config",
    response_model=WhatsAppConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Create/Rotate Tenant WhatsApp Config",
    description=(
        "Create or rotate a tenant's Meta WhatsApp Cloud API credentials. "
        "Super-admin only. Secrets are encrypted at rest and never echoed "
        "back in this or any other response."
    ),
)
async def upsert_tenant_whatsapp_config(
    tenant_id: UUID,
    data: WhatsAppConfigUpsertRequest,
    _context: RequestContext = Depends(
        require_permission(Permission.PLATFORM_WHATSAPP_CONFIG_MANAGE)
    ),
    session: AsyncSession = Depends(get_db_session),
) -> WhatsAppConfigResponse:
    """Upsert tenant WhatsApp config endpoint."""
    service = TenantService(session)
    return await service.upsert_whatsapp_config(tenant_id, data)


@router.get(
    "/{tenant_id}/whatsapp-config",
    response_model=WhatsAppConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Tenant WhatsApp Config",
    description="Fetch a tenant's WhatsApp config metadata (never secrets). Super-admin only.",
)
async def get_tenant_whatsapp_config(
    tenant_id: UUID,
    _context: RequestContext = Depends(
        require_permission(Permission.PLATFORM_WHATSAPP_CONFIG_MANAGE)
    ),
    session: AsyncSession = Depends(get_db_session),
) -> WhatsAppConfigResponse:
    """Get tenant WhatsApp config endpoint."""
    service = TenantService(session)
    return await service.get_whatsapp_config(tenant_id)
```

- [ ] **Step 7: Run the full test suite and lint/type checks**

Run: `cd backend && pytest -q && ruff check app tests && mypy app`
Expected: all pass, no errors.

- [ ] **Step 8: Commit**

```bash
git add app/tenants/schemas.py app/tenants/service.py app/tenants/router.py tests/unit/test_tenants_whatsapp_config_service.py
git commit -m "feat: add super-admin WhatsApp credential admin API"
```

---

## Task 7: Update `app/whatsapp/` to use the Meta client

**Files:**
- Modify: `app/whatsapp/repository.py`
- Modify: `app/whatsapp/service.py`
- Test: `tests/unit/test_whatsapp_repository_customer_resolution.py`
- Test: `tests/unit/test_whatsapp_service_meta_client.py`

**Interfaces:**
- Consumes: `get_client_for_tenant` (Task 5).
- Produces: `WhatsAppRepository.find_customer_by_phone(tenant_id, phone) -> dict | None`, `WhatsAppRepository.create_minimal_customer(tenant_id, phone, full_name) -> dict` — consumed by Task 9's webhook `service.py`.

- [ ] **Step 1: Write the failing repository test**

Create `tests/unit/test_whatsapp_repository_customer_resolution.py`:

```python
"""Unit tests for WhatsAppRepository's tenant-scoped customer lookup/
auto-create, used by the inbound webhook handler for first-contact
numbers."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.whatsapp.repository import WhatsAppRepository


@pytest.mark.asyncio
async def test_find_customer_by_phone_is_tenant_scoped() -> None:
    """Verify the lookup filters by tenant_id, unlike the removed
    cross-tenant method."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    mock_session.execute.return_value = select_res
    repo = WhatsAppRepository(mock_session)
    tenant_id = uuid4()

    result = await repo.find_customer_by_phone(tenant_id, "+919999999999")

    params = mock_session.execute.call_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["phone"] == "+919999999999"
    assert result is not None


@pytest.mark.asyncio
async def test_create_minimal_customer_inserts_required_fields_only() -> None:
    """Verify auto-create for a first-contact WhatsApp sender only sets
    tenant_id/phone/full_name, letting every other column take its schema
    default."""
    mock_session = AsyncMock()
    insert_res = MagicMock()
    new_id = uuid4()
    insert_res.mappings.return_value.one.return_value = {"id": new_id, "full_name": "Jane"}
    mock_session.execute.return_value = insert_res
    repo = WhatsAppRepository(mock_session)
    tenant_id = uuid4()

    result = await repo.create_minimal_customer(tenant_id, "+919999999999", "Jane")

    params = mock_session.execute.call_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["phone"] == "+919999999999"
    assert params["full_name"] == "Jane"
    assert result["id"] == new_id
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_repository_customer_resolution.py -v`
Expected: FAIL — `AttributeError: 'WhatsAppRepository' object has no attribute 'find_customer_by_phone'`.

- [ ] **Step 3: Update `app/whatsapp/repository.py`**

Remove the `find_customer_by_phone_cross_tenant` method entirely (its cross-tenant justification no longer holds now that tenant is always known — from `RequestContext` for outbound sends, from the webhook URL path for inbound). Add these two methods in its place:

```python
    async def find_customer_by_phone(self, tenant_id: UUID, phone: str) -> dict[str, Any] | None:
        """Look up a customer by phone within a tenant (tenant is always
        known here -- from RequestContext for outbound sends, from the
        webhook URL path for inbound messages)."""
        result = await self.session.execute(
            text(
                "SELECT * FROM public.customers "
                "WHERE tenant_id = :tenant_id AND phone = :phone AND deleted_at IS NULL"
            ),
            {"tenant_id": tenant_id, "phone": phone},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def create_minimal_customer(
        self, tenant_id: UUID, phone: str, full_name: str
    ) -> dict[str, Any]:
        """Auto-create a customer for a first-contact inbound WhatsApp
        sender with no existing CRM record. Only the required columns are
        set; every other column takes its schema default."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.customers (tenant_id, phone, full_name)
                VALUES (:tenant_id, :phone, :full_name)
                RETURNING *
                """
            ),
            {"tenant_id": tenant_id, "phone": phone, "full_name": full_name},
        )
        return dict(result.mappings().one())
```

- [ ] **Step 4: Run repository test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_repository_customer_resolution.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the failing service test**

Create `tests/unit/test_whatsapp_service_meta_client.py`:

```python
"""Unit tests verifying WhatsAppService.send_message now goes through the
per-tenant Meta client factory instead of the (deleted) Superfone client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.whatsapp.schemas import WhatsAppSendRequest
from app.whatsapp.service import WhatsAppService


@pytest.mark.asyncio
async def test_send_message_uses_tenant_scoped_meta_client() -> None:
    """Verify send_message resolves the client via get_client_for_tenant
    (tenant-scoped), not a global Superfone client, and that a successful
    send is always stored with status='sent' (Meta's synchronous response
    carries no delivery status -- that arrives later via webhook)."""
    mock_session = AsyncMock()
    service = WhatsAppService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()

    service.repository.get_customer = AsyncMock(
        return_value={"id": customer_id, "phone": "+919999999999"}
    )
    service.repository.get_most_recent_inbound_message = AsyncMock(
        return_value={"created_at": datetime.now(UTC)}
    )
    service.repository.create_message = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "lead_id": None,
            "template_id": None,
            "direction": "outbound",
            "provider_message_id": "wamid.XYZ",
            "wa_id": "919999999999",
            "phone_to": "919999999999",
            "phone_from": None,
            "message_type": "text",
            "content": {"body": "hi"},
            "template_variables": {},
            "status": "sent",
            "delivered_at": None,
            "read_at": None,
            "failed_at": None,
            "failure_code": None,
            "failure_reason": None,
            "sent_at": datetime.now(UTC),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    service.repository.add_communication_log = AsyncMock(return_value=None)

    fake_client = AsyncMock()
    fake_client.send_text_message = AsyncMock(return_value={"message_id": "wamid.XYZ"})

    with patch(
        "app.whatsapp.service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)
    ) as mock_factory:
        await service.send_message(
            tenant_id,
            None,
            WhatsAppSendRequest(
                customer_id=customer_id, message_type="text", message={"body": "hi"}
            ),
        )

    mock_factory.assert_awaited_once_with(mock_session, tenant_id)
    fake_client.send_text_message.assert_awaited_once()
    stored_status = service.repository.create_message.call_args.kwargs["status"]
    assert stored_status == "sent"
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_service_meta_client.py -v`
Expected: FAIL (still imports `get_superfone_whatsapp_client`, or `get_client_for_tenant` patch target doesn't exist yet).

- [ ] **Step 7: Update `app/whatsapp/service.py`**

Replace the Superfone import with the factory import:

```python
# REPLACE:
from app.integrations.superfone.factory import get_superfone_whatsapp_client
# WITH:
from app.integrations.whatsapp.factory import get_client_for_tenant
```

Delete the now-unused `_PROVIDER_STATUS_TO_DB_STATUS` dict and its comment entirely — Meta's synchronous send response carries no delivery status field (unlike Superfone's wrapped API), so a confirmed send is always stored as `"sent"`; delivery/read/failed status arrives later via the webhook (Task 9) and is applied through `update_message_status_by_provider_id`.

In `send_message`, replace:

```python
        client = get_superfone_whatsapp_client()
        recipient = _to_whatsapp_recipient(customer["phone"])
```

with:

```python
        client = await get_client_for_tenant(self.session, tenant_id)
        recipient = _to_whatsapp_recipient(customer["phone"])
```

Update the two call sites that use `recipient=` as a keyword to use `to=` instead (matching `MetaWhatsAppClient`'s signature):

```python
            result = await client.send_template_message(
                to=recipient,
                template_name=template_name,
                language=language,
                components=data.components,
                context_message_id=data.context_message_id,
            )
```

and

```python
            result = await client.send_message(
                to=recipient,
                message_type=data.message_type,
                message=message_body,
                context_message_id=data.context_message_id,
            )
```

Replace this line:

```python
        db_status = _PROVIDER_STATUS_TO_DB_STATUS.get(result.get("message_status") or "", "sent")
```

with:

```python
        db_status = "sent"
```

In `list_templates`:

```python
    async def list_templates(self, tenant_id: UUID, refresh: bool = False) -> list[WhatsAppTemplateResponse]:
        """List WhatsApp templates -- a live passthrough to Meta, not a
        read of the local whatsapp_templates table."""
        client = await get_client_for_tenant(self.session, tenant_id)
        raw_templates = await client.list_templates(refresh=refresh)
        return [
            WhatsAppTemplateResponse(
                id=str(t.get("id", "")),
                name=t.get("name", ""),
                language=t.get("language", ""),
                status=t.get("status", ""),
                category=t.get("category", ""),
                parameter_format=t.get("parameter_format"),
                components=t.get("components", []),
            )
            for t in raw_templates
        ]
```

`list_templates` now needs `tenant_id` — update its one call site in `app/whatsapp/router.py`:

```python
async def list_templates(
    refresh: Annotated[bool, Query(description="Bypass cache, pull fresh from Meta")] = False,
    context: RequestContext = Depends(require_permission(Permission.WHATSAPP_TEMPLATE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[WhatsAppTemplateResponse]:
    """List WhatsApp templates endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to list WhatsApp templates.")
    service = WhatsAppService(session)
    return await service.list_templates(tenant_id, refresh=refresh)
```

(the leading underscore on the `_context` parameter is removed since `context` is now used to resolve `tenant_id`; add `resolve_tenant_scope` to the existing `from app.core.permissions import ...` import line in that router file if not already imported there).

- [ ] **Step 8: Run service test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_service_meta_client.py -v`
Expected: PASS.

- [ ] **Step 9: Run the full test suite and lint/type checks**

Run: `cd backend && pytest -q && ruff check app tests && mypy app`
Expected: all pass. Pay particular attention to any other test referencing `_PROVIDER_STATUS_TO_DB_STATUS`, `get_superfone_whatsapp_client`, or `find_customer_by_phone_cross_tenant` — none should remain (this repo had no pre-existing tests for `app/whatsapp/`, so none are expected, but confirm).

- [ ] **Step 10: Commit**

```bash
git add app/whatsapp/repository.py app/whatsapp/service.py app/whatsapp/router.py tests/unit/test_whatsapp_repository_customer_resolution.py tests/unit/test_whatsapp_service_meta_client.py
git commit -m "feat: switch app/whatsapp/ to the per-tenant Meta WhatsApp client"
```

---

## Task 8: Webhook receiver — signature verification and event schemas

**Files:**
- Create: `app/webhooks/whatsapp/__init__.py`
- Create: `app/webhooks/whatsapp/security.py`
- Create: `app/webhooks/whatsapp/schemas.py`
- Test: `tests/unit/test_whatsapp_webhook_security.py`
- Test: `tests/unit/test_whatsapp_webhook_schemas.py`

**Interfaces:**
- Produces: `verify_meta_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool`, `verify_meta_verify_token(token: str | None, expected: str) -> bool` (both in `security.py`); `parse_webhook_events(payload: dict) -> list[InboundMessageEvent | StatusUpdateEvent | TemplateStatusUpdateEvent]` (in `schemas.py`) — consumed by Task 9.

- [ ] **Step 1: Write the failing security test**

Create `tests/unit/test_whatsapp_webhook_security.py`:

```python
"""Unit tests for Meta webhook authenticity checks: HMAC-SHA256 body
signature (x-hub-signature-256) and the GET subscription handshake token.
Both fail closed."""

import hashlib
import hmac

from app.webhooks.whatsapp.security import verify_meta_signature, verify_meta_verify_token


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_accepts_valid_signature() -> None:
    body = b'{"entry": []}'
    secret = "app-secret-1"
    assert verify_meta_signature(body, _sign(body, secret), secret) is True


def test_verify_signature_rejects_tampered_body() -> None:
    body = b'{"entry": []}'
    secret = "app-secret-1"
    signature = _sign(body, secret)
    tampered_body = b'{"entry": [{"malicious": true}]}'
    assert verify_meta_signature(tampered_body, signature, secret) is False


def test_verify_signature_rejects_missing_header() -> None:
    assert verify_meta_signature(b"{}", None, "app-secret-1") is False


def test_verify_signature_rejects_malformed_header() -> None:
    assert verify_meta_signature(b"{}", "not-sha256-prefixed", "app-secret-1") is False


def test_verify_verify_token_accepts_match() -> None:
    assert verify_meta_verify_token("correct-token", "correct-token") is True


def test_verify_verify_token_rejects_mismatch() -> None:
    assert verify_meta_verify_token("wrong-token", "correct-token") is False


def test_verify_verify_token_rejects_none() -> None:
    assert verify_meta_verify_token(None, "correct-token") is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_webhook_security.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the package and implement `security.py`**

Create `app/webhooks/whatsapp/__init__.py` (empty).

Create `app/webhooks/whatsapp/security.py`:

```python
"""Meta WhatsApp webhook authenticity checks, per tenant.

Unlike Superfone's SFVoPI stream, Meta DOES support HMAC-SHA256 body
signature verification (x-hub-signature-256) and a GET handshake
verify_token, exactly as documented at
https://developers.facebook.com/docs/graph-api/webhooks/getting-started.
Both fail closed: a missing/invalid credential is rejected before any DB
write, matching app/webhooks/superfone/security.py's established pattern.
"""

import hashlib
import hmac


def verify_meta_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Verify the x-hub-signature-256 header against the raw request body,
    using the tenant's own app_secret. Returns False (never raises) --
    callers decide what HTTP status a failed verification maps to."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected_hex = signature_header[len("sha256=") :]
    actual_hex = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(actual_hex, expected_hex)


def verify_meta_verify_token(token: str | None, expected: str) -> bool:
    """Verify the hub.verify_token query param on the GET subscription
    handshake against the tenant's stored verify_token."""
    if not token:
        return False
    return hmac.compare_digest(token, expected)
```

- [ ] **Step 4: Run security test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_webhook_security.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Write the failing schemas test**

Create `tests/unit/test_whatsapp_webhook_schemas.py`:

```python
"""Unit tests for parsing Meta's WhatsApp webhook payload shapes into typed
events. Sample payloads match Meta's documented `messages` field format."""

from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
    parse_webhook_events,
)

_INBOUND_MESSAGE_PAYLOAD = {
    "entry": [
        {
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "770971286099252"},
                        "contacts": [{"profile": {"name": "Jane"}, "wa_id": "919999999999"}],
                        "messages": [
                            {
                                "from": "919999999999",
                                "id": "wamid.ABC123",
                                "timestamp": "1700000000",
                                "type": "text",
                                "text": {"body": "hello"},
                            }
                        ],
                    },
                }
            ]
        }
    ]
}

_STATUS_UPDATE_PAYLOAD = {
    "entry": [
        {
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "770971286099252"},
                        "statuses": [
                            {
                                "id": "wamid.ABC123",
                                "status": "delivered",
                                "timestamp": "1700000100",
                                "recipient_id": "919999999999",
                            }
                        ],
                    },
                }
            ]
        }
    ]
}

_TEMPLATE_STATUS_PAYLOAD = {
    "entry": [
        {
            "changes": [
                {
                    "field": "message_template_status_update",
                    "value": {
                        "message_template_id": 123456,
                        "message_template_name": "greeting",
                        "message_template_language": "hi",
                        "event": "APPROVED",
                    },
                }
            ]
        }
    ]
}


def test_parse_inbound_message_event() -> None:
    events = parse_webhook_events(_INBOUND_MESSAGE_PAYLOAD)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, InboundMessageEvent)
    assert event.from_phone == "919999999999"
    assert event.wa_message_id == "wamid.ABC123"
    assert event.text == "hello"
    assert event.contact_name == "Jane"


def test_parse_status_update_event() -> None:
    events = parse_webhook_events(_STATUS_UPDATE_PAYLOAD)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StatusUpdateEvent)
    assert event.wa_message_id == "wamid.ABC123"
    assert event.status == "delivered"


def test_parse_template_status_update_event() -> None:
    events = parse_webhook_events(_TEMPLATE_STATUS_PAYLOAD)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, TemplateStatusUpdateEvent)
    assert event.provider_template_id == "123456"
    assert event.status == "approved"


def test_parse_ignores_unrecognized_fields() -> None:
    payload = {"entry": [{"changes": [{"field": "business_status_update", "value": {}}]}]}
    assert parse_webhook_events(payload) == []


def test_parse_handles_empty_entry_list() -> None:
    assert parse_webhook_events({"entry": []}) == []
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_webhook_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 7: Implement `schemas.py`**

Create `app/webhooks/whatsapp/schemas.py`:

```python
"""Typed events parsed from Meta's WhatsApp webhook payload. Meta's own
envelope (entry[].changes[].{field,value}) is not modeled 1:1 as Pydantic
input -- parse_webhook_events walks the raw dict defensively (payload
shape is attacker-reachable, so any single malformed entry is skipped
rather than raising and losing every other event in the same delivery)."""

import logging
from typing import Any, Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_STATUS_MAP: dict[str, Literal["sent", "delivered", "read", "failed"]] = {
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "failed": "failed",
}

_TEMPLATE_STATUS_MAP: dict[str, Literal["approved", "rejected", "paused"]] = {
    "APPROVED": "approved",
    "REJECTED": "rejected",
    "PAUSED": "paused",
    "DISABLED": "paused",
}


class InboundMessageEvent(BaseModel):
    """A customer-sent WhatsApp message."""

    from_phone: str
    contact_name: str | None
    wa_message_id: str
    text: str | None
    message_type: str
    timestamp: str


class StatusUpdateEvent(BaseModel):
    """A delivery-status callback for a message this tenant sent."""

    wa_message_id: str
    status: Literal["sent", "delivered", "read", "failed"]
    error_message: str | None = None


class TemplateStatusUpdateEvent(BaseModel):
    """A template approval/rejection/pause status change."""

    provider_template_id: str
    status: Literal["approved", "rejected", "paused"]
    rejection_reason: str | None = None


WebhookEvent = InboundMessageEvent | StatusUpdateEvent | TemplateStatusUpdateEvent


def parse_webhook_events(payload: dict[str, Any]) -> list[WebhookEvent]:
    """Walk Meta's entry[].changes[] envelope and return typed events.
    Any single malformed change is logged and skipped rather than raising
    and losing every other event in the same webhook delivery."""
    events: list[WebhookEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value", {})
            try:
                if field == "messages":
                    events.extend(_parse_messages_change(value))
                elif field == "message_template_status_update":
                    events.append(_parse_template_status_change(value))
            except (KeyError, IndexError, TypeError) as e:
                logger.warning(f"whatsapp webhook: skipping malformed change (field={field!r}): {e!s}")
    return events


def _parse_messages_change(value: dict[str, Any]) -> list[WebhookEvent]:
    events: list[WebhookEvent] = []
    contacts_by_wa_id = {c["wa_id"]: c for c in value.get("contacts", [])}

    for message in value.get("messages", []):
        wa_id = message["from"]
        contact = contacts_by_wa_id.get(wa_id)
        contact_name = contact["profile"]["name"] if contact and contact.get("profile") else None
        message_type = message.get("type", "text")
        text = message.get(message_type, {}).get("body") if message_type == "text" else None
        events.append(
            InboundMessageEvent(
                from_phone=wa_id,
                contact_name=contact_name,
                wa_message_id=message["id"],
                text=text,
                message_type=message_type,
                timestamp=message.get("timestamp", ""),
            )
        )

    for status_entry in value.get("statuses", []):
        raw_status = status_entry["status"]
        mapped_status = _STATUS_MAP.get(raw_status)
        if mapped_status is None:
            logger.warning(f"whatsapp webhook: unrecognized status {raw_status!r}, skipping")
            continue
        errors = status_entry.get("errors") or []
        error_message = errors[0].get("message") if errors else None
        events.append(
            StatusUpdateEvent(
                wa_message_id=status_entry["id"], status=mapped_status, error_message=error_message
            )
        )

    return events


def _parse_template_status_change(value: dict[str, Any]) -> TemplateStatusUpdateEvent:
    raw_status = value["event"]
    mapped_status = _TEMPLATE_STATUS_MAP.get(raw_status, "paused")
    return TemplateStatusUpdateEvent(
        provider_template_id=str(value["message_template_id"]),
        status=mapped_status,
        rejection_reason=value.get("reason"),
    )
```

- [ ] **Step 8: Run schemas test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_webhook_schemas.py -v`
Expected: PASS (5 tests).

- [ ] **Step 9: Run lint/type checks**

Run: `cd backend && ruff check app tests && mypy app`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add app/webhooks/whatsapp/__init__.py app/webhooks/whatsapp/security.py app/webhooks/whatsapp/schemas.py tests/unit/test_whatsapp_webhook_security.py tests/unit/test_whatsapp_webhook_schemas.py
git commit -m "feat: add Meta WhatsApp webhook signature verification and event parsing"
```

---

## Task 9: Webhook receiver — event handlers and router

**Files:**
- Create: `app/webhooks/whatsapp/service.py`
- Create: `app/webhooks/whatsapp/router.py`
- Test: `tests/unit/test_whatsapp_webhook_service.py`
- Test: `tests/unit/test_whatsapp_webhook_router.py`

**Interfaces:**
- Consumes: `WhatsAppTenantConfigRepository.get_decrypted` (Task 4), `WhatsAppRepository.{find_customer_by_phone,create_minimal_customer,create_message,update_message_status_by_provider_id,add_communication_log,get_lead}` (Task 7, plus `get_lead` already existing), `verify_meta_signature`/`verify_meta_verify_token` (Task 8), `parse_webhook_events` (Task 8).
- Produces: `GET/POST /api/v1/webhooks/whatsapp/{tenant_id}` — wired into `app/main.py` in Task 11.

- [ ] **Step 1: Write the failing service test**

Create `tests/unit/test_whatsapp_webhook_service.py`:

```python
"""Unit tests for the inbound WhatsApp webhook event handlers."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
)
from app.webhooks.whatsapp.service import WhatsAppWebhookService


@pytest.mark.asyncio
async def test_handle_inbound_message_with_existing_customer() -> None:
    """Verify an inbound message from a known phone number is stored
    against the existing customer, no new customer created."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()

    service.repository.find_customer_by_phone = AsyncMock(
        return_value={"id": customer_id, "phone": "+919999999999"}
    )
    service.repository.create_minimal_customer = AsyncMock()
    service.repository.get_lead_for_customer = AsyncMock(return_value=None)
    service.repository.create_message = AsyncMock(return_value={"id": uuid4()})
    service.repository.add_communication_log = AsyncMock()

    event = InboundMessageEvent(
        from_phone="919999999999",
        contact_name="Jane",
        wa_message_id="wamid.ABC123",
        text="hello",
        message_type="text",
        timestamp="1700000000",
    )

    await service.handle_inbound_message(tenant_id, event)

    service.repository.create_minimal_customer.assert_not_called()
    service.repository.create_message.assert_awaited_once()
    create_kwargs = service.repository.create_message.call_args.kwargs
    assert create_kwargs["customer_id"] == customer_id
    assert create_kwargs["direction"] == "inbound"
    assert create_kwargs["provider_message_id"] == "wamid.ABC123"


@pytest.mark.asyncio
async def test_handle_inbound_message_auto_creates_first_contact_customer() -> None:
    """Verify an inbound message from an unknown phone number auto-creates
    a minimal customer before storing the message."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)
    tenant_id = uuid4()
    new_customer_id = uuid4()

    service.repository.find_customer_by_phone = AsyncMock(return_value=None)
    service.repository.create_minimal_customer = AsyncMock(
        return_value={"id": new_customer_id}
    )
    service.repository.get_lead_for_customer = AsyncMock(return_value=None)
    service.repository.create_message = AsyncMock(return_value={"id": uuid4()})
    service.repository.add_communication_log = AsyncMock()

    event = InboundMessageEvent(
        from_phone="919999999999",
        contact_name="Jane",
        wa_message_id="wamid.ABC123",
        text="hello",
        message_type="text",
        timestamp="1700000000",
    )

    await service.handle_inbound_message(tenant_id, event)

    service.repository.create_minimal_customer.assert_awaited_once_with(
        tenant_id, "+919999999999", "Jane"
    )
    create_kwargs = service.repository.create_message.call_args.kwargs
    assert create_kwargs["customer_id"] == new_customer_id


@pytest.mark.asyncio
async def test_handle_status_update_known_message() -> None:
    """Verify a status callback updates the matching message and logs it."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)

    service.repository.update_message_status_by_provider_id = AsyncMock(
        return_value={
            "id": uuid4(),
            "tenant_id": uuid4(),
            "customer_id": uuid4(),
            "lead_id": None,
        }
    )
    service.repository.add_communication_log = AsyncMock()

    event = StatusUpdateEvent(wa_message_id="wamid.ABC123", status="delivered")
    await service.handle_status_update(event)

    service.repository.add_communication_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_status_update_unknown_message_is_a_noop() -> None:
    """Verify a status event for a wamid this tenant never sent is skipped,
    not an error."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)

    service.repository.update_message_status_by_provider_id = AsyncMock(return_value=None)
    service.repository.add_communication_log = AsyncMock()

    event = StatusUpdateEvent(wa_message_id="wamid.UNKNOWN", status="delivered")
    await service.handle_status_update(event)

    service.repository.add_communication_log.assert_not_called()


@pytest.mark.asyncio
async def test_handle_template_status_update() -> None:
    """Verify a template approval event updates the matching template row."""
    mock_session = AsyncMock()
    service = WhatsAppWebhookService(mock_session)
    service.repository.update_template_status_by_provider_id = AsyncMock()

    event = TemplateStatusUpdateEvent(provider_template_id="123456", status="approved")
    await service.handle_template_status_update(event)

    service.repository.update_template_status_by_provider_id.assert_awaited_once_with(
        "123456", "approved", None
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_webhook_service.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Add `update_template_status_by_provider_id` to `WhatsAppRepository`**

In `app/whatsapp/repository.py`, add:

```python
    async def update_template_status_by_provider_id(
        self, provider_template_id: str, status: str, rejection_reason: str | None
    ) -> dict[str, Any] | None:
        """Update a template's approval status by Meta's template ID."""
        result = await self.session.execute(
            text(
                """
                UPDATE public.whatsapp_templates
                SET status = :status::public.whatsapp_template_status,
                    rejection_reason = :rejection_reason,
                    updated_at = NOW()
                WHERE provider_template_id = :provider_template_id
                RETURNING *
                """
            ),
            {
                "provider_template_id": provider_template_id,
                "status": status,
                "rejection_reason": rejection_reason,
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None
```

- [ ] **Step 4: Implement `service.py`**

Create `app/webhooks/whatsapp/service.py`:

```python
"""Inbound WhatsApp webhook event handlers -- persist inbound messages,
delivery-status updates, and template status updates. No auto-reply logic
in this phase (spec Non-Goals)."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
)
from app.whatsapp.repository import WhatsAppRepository

logger = logging.getLogger(__name__)


class WhatsAppWebhookService:
    """Processes parsed WhatsApp webhook events for one tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = WhatsAppRepository(session)

    async def handle_inbound_message(self, tenant_id: UUID, event: InboundMessageEvent) -> None:
        """Resolve/auto-create the customer, persist the message and a
        communication_logs entry. No AI reply is generated."""
        phone = f"+{event.from_phone}"
        customer = await self.repository.find_customer_by_phone(tenant_id, phone)
        if not customer:
            customer = await self.repository.create_minimal_customer(
                tenant_id, phone, event.contact_name or phone
            )

        lead = await self.repository.get_lead_for_customer(tenant_id, customer["id"])
        lead_id = lead["id"] if lead else None

        message_row = await self.repository.create_message(
            tenant_id=tenant_id,
            customer_id=customer["id"],
            lead_id=lead_id,
            direction="inbound",
            provider_message_id=event.wa_message_id,
            wa_id=event.from_phone,
            phone_to=None,
            phone_from=event.from_phone,
            message_type=event.message_type,
            content={"body": event.text} if event.text else {},
            template_variables={},
            status="delivered",
            sent_at=None,
        )

        await self.repository.add_communication_log(
            tenant_id=tenant_id,
            customer_id=customer["id"],
            lead_id=lead_id,
            whatsapp_message_id=message_row["id"],
            direction="inbound",
            status="delivered",
            summary="WhatsApp message received",
            initiated_by="system",
            initiated_by_id=None,
        )

    async def handle_status_update(self, event: StatusUpdateEvent) -> None:
        """Update the matching message's delivery status. A status event
        for a wamid this tenant never sent through this integration is
        skipped, not an error (e.g. a message sent before this pipeline
        existed)."""
        updated = await self.repository.update_message_status_by_provider_id(
            event.wa_message_id,
            event.status,
            delivered_at=None,
            read_at=None,
            failed_at=None,
            failure_code=None,
            failure_reason=event.error_message,
        )
        if not updated:
            logger.info(f"whatsapp webhook: status update for unknown wamid {event.wa_message_id!r}, skipping")
            return

        await self.repository.add_communication_log(
            tenant_id=updated["tenant_id"],
            customer_id=updated["customer_id"],
            lead_id=updated["lead_id"],
            whatsapp_message_id=updated["id"],
            direction="outbound",
            status=event.status,
            summary=f"WhatsApp message {event.status}",
            initiated_by="system",
            initiated_by_id=None,
        )

    async def handle_template_status_update(self, event: TemplateStatusUpdateEvent) -> None:
        """Update the matching template's approval status."""
        await self.repository.update_template_status_by_provider_id(
            event.provider_template_id, event.status, event.rejection_reason
        )
```

`get_lead_for_customer` does not exist yet on `WhatsAppRepository` (the existing `get_lead` takes a `lead_id`, not a `customer_id`) — add it to `app/whatsapp/repository.py`:

```python
    async def get_lead_for_customer(self, tenant_id: UUID, customer_id: UUID) -> dict[str, Any] | None:
        """Fetch the most recently created lead for a customer, if any."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.leads
                WHERE tenant_id = :tenant_id AND customer_id = :customer_id
                  AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "customer_id": customer_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None
```

- [ ] **Step 5: Run service test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_webhook_service.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Write the failing router test**

Create `tests/unit/test_whatsapp_webhook_router.py`:

```python
"""Unit tests for the tenant-scoped WhatsApp webhook endpoints, exercised
through the FastAPI test client (tests/conftest.py's async_client fixture)."""

import hashlib
import hmac
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest


def _sign(body: bytes, secret: str) -> str:
    return f"sha256={hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}"


@pytest.mark.asyncio
async def test_get_handshake_echoes_challenge_on_valid_token(async_client) -> None:
    tenant_id = uuid4()
    with patch(
        "app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"verify_token": "correct-token", "is_active": True}
        )
        response = await async_client.get(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "correct-token",
                "hub.challenge": "challenge123",
            },
        )
    assert response.status_code == 200
    assert response.text == "challenge123"


@pytest.mark.asyncio
async def test_get_handshake_rejects_wrong_token(async_client) -> None:
    tenant_id = uuid4()
    with patch(
        "app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"verify_token": "correct-token", "is_active": True}
        )
        response = await async_client.get(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge123",
            },
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_handshake_404s_for_unconfigured_tenant(async_client) -> None:
    with patch(
        "app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(return_value=None)
        response = await async_client.get(
            f"/api/v1/webhooks/whatsapp/{uuid4()}",
            params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "y"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_post_rejects_invalid_signature(async_client) -> None:
    tenant_id = uuid4()
    body = b'{"entry": []}'
    with patch(
        "app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"app_secret": "correct-secret", "is_active": True}
        )
        response = await async_client.post(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            content=body,
            headers={"x-hub-signature-256": _sign(body, "wrong-secret")},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_accepts_valid_signature_and_processes_events(async_client) -> None:
    tenant_id = uuid4()
    body = b'{"entry": []}'
    with (
        patch("app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository") as mock_repo_cls,
        patch("app.webhooks.whatsapp.router.WhatsAppWebhookService") as mock_service_cls,
    ):
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"app_secret": "correct-secret", "is_active": True}
        )
        response = await async_client.post(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            content=body,
            headers={"x-hub-signature-256": _sign(body, "correct-secret")},
        )
    assert response.status_code == 200
    assert response.text == "EVENT_RECEIVED"


@pytest.mark.asyncio
async def test_post_returns_200_even_when_event_processing_raises(async_client) -> None:
    """A validly-signed POST always answers 200, even when processing
    throws -- sustained 5xx responses cause Meta to disable the
    subscription, which is worse than losing one event to a logged error."""
    tenant_id = uuid4()
    body = b'{"entry": [{"changes": [{"field": "messages", "value": {"messages": [{"from": "1", "id": "x", "type": "text"}]}}]}]}'
    with (
        patch("app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository") as mock_repo_cls,
        patch("app.webhooks.whatsapp.router.WhatsAppWebhookService") as mock_service_cls,
    ):
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"app_secret": "correct-secret", "is_active": True}
        )
        mock_service_cls.return_value.handle_inbound_message = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        response = await async_client.post(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            content=body,
            headers={"x-hub-signature-256": _sign(body, "correct-secret")},
        )
    assert response.status_code == 200
```

- [ ] **Step 7: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_webhook_router.py -v`
Expected: FAIL — router module does not exist / not registered on `app` yet (404 on all routes).

- [ ] **Step 8: Implement `router.py`**

Create `app/webhooks/whatsapp/router.py`:

```python
"""Tenant-scoped Meta WhatsApp webhook receiver.

Routes by tenant_id in the URL path -- never by parsing the (still
unverified) payload first. Each tenant registers their own Meta App's
webhook subscription pointing at
`{APP_PUBLIC_BASE_URL}/api/v1/webhooks/whatsapp/{their_tenant_id}`.
"""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.integrations.whatsapp.repository import WhatsAppTenantConfigRepository
from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
    parse_webhook_events,
)
from app.webhooks.whatsapp.security import verify_meta_signature, verify_meta_verify_token
from app.webhooks.whatsapp.service import WhatsAppWebhookService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp Webhooks"])


@router.get("/{tenant_id}")
async def whatsapp_webhook_handshake(
    tenant_id: UUID,
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Meta's webhook subscription verification handshake."""
    repo = WhatsAppTenantConfigRepository(session)
    config = await repo.get_decrypted(tenant_id)
    if not config or not config["is_active"]:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    if hub_mode == "subscribe" and verify_meta_verify_token(hub_verify_token, config["verify_token"]):
        return Response(content=hub_challenge, status_code=status.HTTP_200_OK)
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/{tenant_id}")
async def whatsapp_webhook_receive(
    tenant_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Receive and process a Meta WhatsApp webhook delivery. Always returns
    200 once signature verification passes, even if event processing
    raises -- a validly-signed request that fails internally will fail
    identically on retry, while sustained webhook failures cause Meta to
    disable the subscription outright."""
    repo = WhatsAppTenantConfigRepository(session)
    config = await repo.get_decrypted(tenant_id)
    if not config or not config["is_active"]:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_meta_signature(raw_body, signature, config["app_secret"]):
        logger.warning(f"whatsapp webhook: invalid signature for tenant {tenant_id}")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        payload = json.loads(raw_body)
        events = parse_webhook_events(payload)
        service = WhatsAppWebhookService(session)
        for event in events:
            if isinstance(event, InboundMessageEvent):
                await service.handle_inbound_message(tenant_id, event)
            elif isinstance(event, StatusUpdateEvent):
                await service.handle_status_update(event)
            elif isinstance(event, TemplateStatusUpdateEvent):
                await service.handle_template_status_update(event)
        await session.commit()
    except Exception:
        logger.exception(f"whatsapp webhook: failed to process payload for tenant {tenant_id}")

    return Response(content="EVENT_RECEIVED", status_code=status.HTTP_200_OK)
```

- [ ] **Step 9: Run router test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_webhook_router.py -v`
Expected: FAIL initially (router not yet registered in `app.main` — that happens in Task 11). Note this in the task and proceed; Task 11 will make these pass. Alternatively, register the router in `app/main.py` now as a small addition to this task rather than deferring to Task 11 — do that instead, since a router with no test able to reach it is not truly done:

In `app/main.py`, add the import:

```python
from app.webhooks.whatsapp.router import router as whatsapp_webhooks_router
```

and registration (grouped with the other webhook routers):

```python
app.include_router(whatsapp_webhooks_router, prefix=API_V1_PREFIX)
```

Then re-run: `cd backend && pytest tests/unit/test_whatsapp_webhook_router.py -v`
Expected: PASS (6 tests).

- [ ] **Step 10: Run the full test suite and lint/type checks**

Run: `cd backend && pytest -q && ruff check app tests && mypy app`
Expected: all pass, no errors. Fix any import-ordering issues `ruff` flags from Step 8's inline-import writeup.

- [ ] **Step 11: Commit**

```bash
git add app/webhooks/whatsapp/service.py app/webhooks/whatsapp/router.py app/whatsapp/repository.py app/main.py tests/unit/test_whatsapp_webhook_service.py tests/unit/test_whatsapp_webhook_router.py
git commit -m "feat: add tenant-scoped Meta WhatsApp webhook receiver"
```

---

## Task 10: Call-trigger endpoint for the dashboard integration

**Files:**
- Create: `app/webhooks/whatsapp_dashboard/__init__.py`
- Create: `app/webhooks/whatsapp_dashboard/security.py`
- Create: `app/webhooks/whatsapp_dashboard/repository.py`
- Create: `app/webhooks/whatsapp_dashboard/schemas.py`
- Create: `app/webhooks/whatsapp_dashboard/service.py`
- Create: `app/webhooks/whatsapp_dashboard/router.py`
- Test: `tests/unit/test_whatsapp_dashboard_security.py`
- Test: `tests/unit/test_whatsapp_dashboard_repository.py`
- Test: `tests/unit/test_whatsapp_dashboard_service.py`
- Test: `tests/unit/test_whatsapp_dashboard_router.py`

**Interfaces:**
- Consumes: `AgentGateway.prepare_call`/`start_call` (existing, `app/agent/gateway.py`), `CallOrchestrator.create_call_job` (existing, `app/agent/orchestrator.py`), `settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET` (Task 2).
- Produces: `POST /api/v1/webhooks/whatsapp-dashboard/call-agent` — wired into `app/main.py` in this task.

- [ ] **Step 1: Write the failing security test**

Create `tests/unit/test_whatsapp_dashboard_security.py`:

```python
"""Unit tests for the whatsapp-dashboard call-agent bearer check. Mirrors
tests/unit/test_superfone_webhook_security.py's verify_superfone_crm_bearer
coverage exactly."""

from unittest.mock import patch

import pytest

from app.core.exceptions import UnauthorizedError
from app.webhooks.whatsapp_dashboard.security import verify_call_agent_bearer


class _FakeSecretStr:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def test_accepts_matching_token() -> None:
    with patch("app.webhooks.whatsapp_dashboard.security.settings") as mock_settings:
        mock_settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET = _FakeSecretStr("secret-1")
        verify_call_agent_bearer("Bearer secret-1")  # must not raise


def test_rejects_wrong_token() -> None:
    with patch("app.webhooks.whatsapp_dashboard.security.settings") as mock_settings:
        mock_settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET = _FakeSecretStr("secret-1")
        with pytest.raises(UnauthorizedError):
            verify_call_agent_bearer("Bearer wrong")


def test_rejects_missing_header() -> None:
    with patch("app.webhooks.whatsapp_dashboard.security.settings") as mock_settings:
        mock_settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET = _FakeSecretStr("secret-1")
        with pytest.raises(UnauthorizedError):
            verify_call_agent_bearer(None)


def test_fails_closed_when_unconfigured() -> None:
    with patch("app.webhooks.whatsapp_dashboard.security.settings") as mock_settings:
        mock_settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET = _FakeSecretStr("")
        with pytest.raises(UnauthorizedError):
            verify_call_agent_bearer("Bearer anything")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_dashboard_security.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the package and implement `security.py`**

Create `app/webhooks/whatsapp_dashboard/__init__.py` (empty).

Create `app/webhooks/whatsapp_dashboard/security.py`:

```python
"""Bearer-token check for the whatsapp_busness_dashboard product's
call-agent trigger requests. That product is a separate, unmodified
repository whose stub `triggerCallAgent` posts a static Authorization:
Bearer header -- this mirrors
app/webhooks/superfone/security.py::verify_superfone_crm_bearer exactly."""

import hmac

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.core.security import extract_bearer_token


def verify_call_agent_bearer(authorization_header: str | None) -> None:
    """Validate the Authorization: Bearer header against
    WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET. Fails closed."""
    expected = settings.WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET.get_secret_value()
    if not expected:
        raise UnauthorizedError(
            message="WhatsApp dashboard call-agent authentication is not configured.",
            code="CALL_AGENT_WEBHOOK_NOT_CONFIGURED",
        )
    token = extract_bearer_token(authorization_header)
    if not hmac.compare_digest(token, expected):
        raise UnauthorizedError(
            message="Invalid WhatsApp dashboard call-agent bearer token.",
            code="CALL_AGENT_WEBHOOK_INVALID_TOKEN",
        )
```

- [ ] **Step 4: Run security test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_dashboard_security.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing repository test**

Create `tests/unit/test_whatsapp_dashboard_repository.py`:

```python
"""Unit tests for cross-tenant phone->customer/lead resolution used only by
the whatsapp-dashboard call-agent trigger (the one legitimate remaining use
of an unscoped phone lookup, since the caller supplies no tenant)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.webhooks.whatsapp_dashboard.repository import CallAgentTriggerRepository


@pytest.mark.asyncio
async def test_find_customer_returns_unambiguous_single_match() -> None:
    mock_session = AsyncMock()
    select_res = MagicMock()
    tenant_id = uuid4()
    customer_id = uuid4()
    select_res.mappings.return_value.all.return_value = [
        {"id": customer_id, "tenant_id": tenant_id, "phone": "+919999999999"}
    ]
    mock_session.execute.return_value = select_res
    repo = CallAgentTriggerRepository(mock_session)

    result = await repo.find_customer_by_phone_cross_tenant("+919999999999")

    assert result is not None
    assert result["id"] == customer_id


@pytest.mark.asyncio
async def test_find_customer_returns_none_on_zero_matches() -> None:
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.all.return_value = []
    mock_session.execute.return_value = select_res
    repo = CallAgentTriggerRepository(mock_session)

    assert await repo.find_customer_by_phone_cross_tenant("+919999999999") is None


@pytest.mark.asyncio
async def test_find_customer_returns_none_on_ambiguous_matches() -> None:
    """Two different tenants both having a customer with this phone number
    is treated as unattributable, never guessed."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.all.return_value = [
        {"id": uuid4(), "tenant_id": uuid4()},
        {"id": uuid4(), "tenant_id": uuid4()},
    ]
    mock_session.execute.return_value = select_res
    repo = CallAgentTriggerRepository(mock_session)

    assert await repo.find_customer_by_phone_cross_tenant("+919999999999") is None


@pytest.mark.asyncio
async def test_get_or_create_lead_returns_existing_lead() -> None:
    mock_session = AsyncMock()
    select_res = MagicMock()
    lead_id = uuid4()
    select_res.mappings.return_value.one_or_none.return_value = {"id": lead_id}
    mock_session.execute.return_value = select_res
    repo = CallAgentTriggerRepository(mock_session)

    result = await repo.get_or_create_lead(uuid4(), uuid4())

    assert result["id"] == lead_id
    assert mock_session.execute.call_count == 1


@pytest.mark.asyncio
async def test_get_or_create_lead_creates_minimal_lead_when_none_exists() -> None:
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = None
    insert_res = MagicMock()
    new_lead_id = uuid4()
    insert_res.mappings.return_value.one.return_value = {"id": new_lead_id}
    mock_session.execute.side_effect = [select_res, insert_res]
    repo = CallAgentTriggerRepository(mock_session)

    result = await repo.get_or_create_lead(uuid4(), uuid4())

    assert result["id"] == new_lead_id
    assert mock_session.execute.call_count == 2
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_dashboard_repository.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 7: Implement `repository.py`**

Create `app/webhooks/whatsapp_dashboard/repository.py`:

```python
"""Repository for the whatsapp-dashboard call-agent trigger: cross-tenant
phone lookup (the one legitimate remaining use, since the caller supplies
no tenant) and lead resolution/auto-create (call_jobs.lead_id is NOT NULL,
so a lead is mandatory before a call_job can be created)."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class CallAgentTriggerRepository:
    """Repository backing the whatsapp-dashboard call-agent trigger flow."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_customer_by_phone_cross_tenant(self, phone: str) -> dict[str, Any] | None:
        """Look up a customer by phone WITHOUT a tenant_id filter. Returns
        the single matching row ONLY if the phone number is unambiguous
        (exactly one non-deleted customer across ALL tenants has it) -- if
        zero or two-or-more tenants have a customer with this phone,
        returns None so the caller treats it as unattributable rather than
        guessing which tenant it belongs to."""
        result = await self.session.execute(
            text("SELECT * FROM public.customers WHERE phone = :phone AND deleted_at IS NULL"),
            {"phone": phone},
        )
        rows = result.mappings().all()
        if len(rows) != 1:
            return None
        return dict(rows[0])

    async def get_or_create_lead(self, tenant_id: UUID, customer_id: UUID) -> dict[str, Any]:
        """Fetch the customer's most recent lead, or auto-create a minimal
        one. call_jobs.lead_id is NOT NULL, so this is mandatory before
        queuing/placing a call."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.leads
                WHERE tenant_id = :tenant_id AND customer_id = :customer_id
                  AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "customer_id": customer_id},
        )
        row = result.mappings().one_or_none()
        if row:
            return dict(row)

        insert_result = await self.session.execute(
            text(
                """
                INSERT INTO public.leads (tenant_id, customer_id)
                VALUES (:tenant_id, :customer_id)
                RETURNING *
                """
            ),
            {"tenant_id": tenant_id, "customer_id": customer_id},
        )
        return dict(insert_result.mappings().one())
```

- [ ] **Step 8: Run repository test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_dashboard_repository.py -v`
Expected: PASS (5 tests).

- [ ] **Step 9: Write the failing service test**

Create `tests/unit/test_whatsapp_dashboard_service.py`:

```python
"""Unit tests for the call-agent trigger orchestration: reason='requested'
places an immediate call (prepare_call -> start_call); anything else
queues a normal-priority call_job."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.webhooks.whatsapp_dashboard.schemas import CallAgentTriggerRequest
from app.webhooks.whatsapp_dashboard.service import CallAgentTriggerService


@pytest.mark.asyncio
async def test_unattributable_phone_returns_customer_not_found() -> None:
    mock_session = AsyncMock()
    service = CallAgentTriggerService(mock_session)
    service.repository.find_customer_by_phone_cross_tenant = AsyncMock(return_value=None)

    result = await service.trigger(CallAgentTriggerRequest(phone="+919999999999", reason="requested"))

    assert result == {"success": False, "error_code": "CUSTOMER_NOT_FOUND"}


@pytest.mark.asyncio
async def test_reason_requested_places_immediate_call() -> None:
    mock_session = AsyncMock()
    service = CallAgentTriggerService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()
    lead_id = uuid4()
    job_id = uuid4()

    service.repository.find_customer_by_phone_cross_tenant = AsyncMock(
        return_value={"id": customer_id, "tenant_id": tenant_id, "phone": "+919999999999"}
    )
    service.repository.get_or_create_lead = AsyncMock(return_value={"id": lead_id})

    with (
        patch("app.webhooks.whatsapp_dashboard.service.CallOrchestrator") as mock_orch_cls,
        patch("app.webhooks.whatsapp_dashboard.service.AgentGateway") as mock_gateway_cls,
    ):
        mock_orch_cls.return_value.create_call_job = AsyncMock(return_value={"id": job_id})
        mock_gateway_cls.return_value.prepare_call = AsyncMock(return_value={"job": {}})
        mock_gateway_cls.return_value.start_call = AsyncMock(return_value={"success": True})

        result = await service.trigger(
            CallAgentTriggerRequest(phone="+919999999999", reason="requested")
        )

    mock_orch_cls.return_value.create_call_job.assert_awaited_once_with(
        tenant_id=tenant_id,
        lead_id=lead_id,
        customer_id=customer_id,
        job_type="whatsapp_callback_request",
        priority=1,
    )
    mock_gateway_cls.return_value.prepare_call.assert_awaited_once_with(tenant_id, job_id)
    mock_gateway_cls.return_value.start_call.assert_awaited_once_with(tenant_id, job_id)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_other_reason_queues_call_job_without_placing_call() -> None:
    mock_session = AsyncMock()
    service = CallAgentTriggerService(mock_session)
    tenant_id = uuid4()
    customer_id = uuid4()
    lead_id = uuid4()
    job_id = uuid4()

    service.repository.find_customer_by_phone_cross_tenant = AsyncMock(
        return_value={"id": customer_id, "tenant_id": tenant_id, "phone": "+919999999999"}
    )
    service.repository.get_or_create_lead = AsyncMock(return_value={"id": lead_id})

    with (
        patch("app.webhooks.whatsapp_dashboard.service.CallOrchestrator") as mock_orch_cls,
        patch("app.webhooks.whatsapp_dashboard.service.AgentGateway") as mock_gateway_cls,
    ):
        mock_orch_cls.return_value.create_call_job = AsyncMock(return_value={"id": job_id})

        result = await service.trigger(
            CallAgentTriggerRequest(phone="+919999999999", reason="first_message")
        )

    mock_orch_cls.return_value.create_call_job.assert_awaited_once_with(
        tenant_id=tenant_id,
        lead_id=lead_id,
        customer_id=customer_id,
        job_type="whatsapp_callback_request",
        priority=5,
    )
    mock_gateway_cls.return_value.prepare_call.assert_not_awaited()
    mock_gateway_cls.return_value.start_call.assert_not_awaited()
    assert result["success"] is True
```

- [ ] **Step 10: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_dashboard_service.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 11: Implement `schemas.py` and `service.py`**

Create `app/webhooks/whatsapp_dashboard/schemas.py`:

```python
"""Request schema for the whatsapp-dashboard call-agent trigger. Shape is
fixed by the (unmodified, external) dashboard product's stub
triggerCallAgent -- this backend cannot dictate it."""

from pydantic import BaseModel


class CallAgentTriggerRequest(BaseModel):
    """{"phone": ..., "reason": ...} exactly as posted by
    whatsapp_busness_dashboard's lib/ai/tools.ts::triggerCallAgent."""

    phone: str
    reason: str
```

Create `app/webhooks/whatsapp_dashboard/service.py`:

```python
"""Orchestrates the whatsapp-dashboard call-agent trigger: resolve the
caller's phone number to a tenant/customer/lead, then either place an
immediate outbound call (reason='requested') or queue a normal-priority
call_job for whatever future dispatcher drives it (any other reason)."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.gateway import AgentGateway
from app.agent.orchestrator import CallOrchestrator
from app.webhooks.whatsapp_dashboard.repository import CallAgentTriggerRepository
from app.webhooks.whatsapp_dashboard.schemas import CallAgentTriggerRequest

logger = logging.getLogger(__name__)

_IMMEDIATE_PRIORITY = 1
_QUEUED_PRIORITY = 5
_JOB_TYPE = "whatsapp_callback_request"


class CallAgentTriggerService:
    """Handles POST /webhooks/whatsapp-dashboard/call-agent."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = CallAgentTriggerRepository(session)

    async def trigger(self, data: CallAgentTriggerRequest) -> dict[str, Any]:
        """Best-effort by design: the caller's own fetch is wrapped in
        try/catch and ignores the response, so failures here are logged
        and returned as a clean payload, never raised."""
        customer = await self.repository.find_customer_by_phone_cross_tenant(data.phone)
        if not customer:
            logger.info(f"call-agent trigger: no unambiguous customer match for phone (reason={data.reason!r})")
            return {"success": False, "error_code": "CUSTOMER_NOT_FOUND"}

        tenant_id = customer["tenant_id"]
        customer_id = customer["id"]
        lead = await self.repository.get_or_create_lead(tenant_id, customer_id)
        lead_id = lead["id"]

        orchestrator = CallOrchestrator(self.session)
        priority = _IMMEDIATE_PRIORITY if data.reason == "requested" else _QUEUED_PRIORITY
        job = await orchestrator.create_call_job(
            tenant_id=tenant_id,
            lead_id=lead_id,
            customer_id=customer_id,
            job_type=_JOB_TYPE,
            priority=priority,
        )

        if data.reason != "requested":
            return {"success": True, "data": {"call_job_id": str(job["id"]), "queued": True}}

        gateway = AgentGateway(self.session)
        await gateway.prepare_call(tenant_id, job["id"])
        result = await gateway.start_call(tenant_id, job["id"])
        return {"success": bool(result.get("success", True)), "data": result}
```

- [ ] **Step 12: Run service test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_dashboard_service.py -v`
Expected: PASS (3 tests).

- [ ] **Step 13: Write the failing router test**

Create `tests/unit/test_whatsapp_dashboard_router.py`:

```python
"""Unit tests for the call-agent trigger HTTP endpoint."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_rejects_missing_bearer(async_client) -> None:
    response = await async_client.post(
        "/api/v1/webhooks/whatsapp-dashboard/call-agent",
        json={"phone": "+919999999999", "reason": "requested"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_accepts_valid_bearer_and_delegates_to_service(async_client) -> None:
    with (
        patch("app.webhooks.whatsapp_dashboard.router.verify_call_agent_bearer"),
        patch("app.webhooks.whatsapp_dashboard.router.CallAgentTriggerService") as mock_service_cls,
    ):
        mock_service_cls.return_value.trigger = AsyncMock(
            return_value={"success": True, "data": {}}
        )
        response = await async_client.post(
            "/api/v1/webhooks/whatsapp-dashboard/call-agent",
            json={"phone": "+919999999999", "reason": "requested"},
            headers={"Authorization": "Bearer whatever"},
        )
    assert response.status_code == 200
    assert response.json()["success"] is True
```

- [ ] **Step 14: Run it to verify it fails**

Run: `cd backend && pytest tests/unit/test_whatsapp_dashboard_router.py -v`
Expected: FAIL — 404 (route doesn't exist yet).

- [ ] **Step 15: Implement `router.py` and wire it into `app/main.py`**

Create `app/webhooks/whatsapp_dashboard/router.py`:

```python
"""HTTP endpoint the whatsapp_busness_dashboard product's stub
triggerCallAgent posts to. See app/webhooks/whatsapp_dashboard/service.py
for the orchestration this delegates to."""

from typing import Any

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.webhooks.whatsapp_dashboard.schemas import CallAgentTriggerRequest
from app.webhooks.whatsapp_dashboard.security import verify_call_agent_bearer
from app.webhooks.whatsapp_dashboard.service import CallAgentTriggerService

router = APIRouter(prefix="/webhooks/whatsapp-dashboard", tags=["WhatsApp Dashboard Integration"])


@router.post(
    "/call-agent",
    status_code=status.HTTP_200_OK,
    summary="WhatsApp Dashboard Call-Agent Trigger",
    description=(
        "Called by the whatsapp_busness_dashboard product's WhatsApp AI "
        "agent when a customer asks for a human, or on first contact. "
        "Resolves the phone number to a tenant/customer/lead and either "
        "places an immediate outbound call or queues one."
    ),
)
async def trigger_call_agent(
    data: CallAgentTriggerRequest,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Call-agent trigger endpoint."""
    verify_call_agent_bearer(authorization)
    service = CallAgentTriggerService(session)
    return await service.trigger(data)
```

In `app/main.py`, add the import:

```python
from app.webhooks.whatsapp_dashboard.router import router as whatsapp_dashboard_router
```

and registration:

```python
app.include_router(whatsapp_dashboard_router, prefix=API_V1_PREFIX)
```

- [ ] **Step 16: Run router test to verify it passes**

Run: `cd backend && pytest tests/unit/test_whatsapp_dashboard_router.py -v`
Expected: PASS (2 tests).

- [ ] **Step 17: Run the full test suite and lint/type checks**

Run: `cd backend && pytest -q && ruff check app tests && mypy app`
Expected: all pass, no errors.

- [ ] **Step 18: Commit**

```bash
git add app/webhooks/whatsapp_dashboard/ app/main.py tests/unit/test_whatsapp_dashboard_security.py tests/unit/test_whatsapp_dashboard_repository.py tests/unit/test_whatsapp_dashboard_service.py tests/unit/test_whatsapp_dashboard_router.py
git commit -m "feat: add whatsapp-dashboard call-agent trigger endpoint"
```

---

## Task 11: Final regression pass and README update

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-10.

- [ ] **Step 1: Update the README's "Not Yet Implemented" section**

In `backend/README.md`, remove the stale bullet:

```markdown
- **WhatsApp messaging** — DB schema is ready (`whatsapp_templates`, `whatsapp_messages`, `communication_logs` in `008_communication.sql`), but there's no `app/` module or router sending/receiving messages yet.
```

Add a new "Phase 5" features section (after the existing "Phase 4 — AI Voice Agent" section, before "Directory Structure"):

```markdown
### Phase 5 — Multi-Tenant Direct-Meta WhatsApp Integration

- **Per-tenant Meta credentials**: `whatsapp_tenant_configs` (migration 029) stores each tenant's WABA ID, phone number ID, and Fernet-encrypted access token/app secret. Managed via a super-admin-only admin API (`PUT`/`GET /api/v1/tenants/{tenant_id}/whatsapp-config`).
- **Send/list**: `app/whatsapp/` — send a message (`POST /api/v1/whatsapp/messages`, template or free-form within the 24-hour session window), list message history, list live-synced templates. Goes directly to Meta's Graph API per tenant, no intermediary provider.
- **Inbound webhook**: `GET`/`POST /api/v1/webhooks/whatsapp/{tenant_id}` — tenant-scoped Meta webhook receiver (routed by tenant ID in the URL path, HMAC-SHA256 signature verified per tenant). Persists inbound messages (auto-creating a minimal customer record for first-contact numbers) and delivery/template status updates. No AI auto-reply in this phase.
- **Dashboard call-escalation bridge**: `POST /api/v1/webhooks/whatsapp-dashboard/call-agent` — a narrow, separately-authenticated endpoint serving the one WhatsApp Business dashboard product still running its own (unrelated) Meta integration for a single tenant. Resolves phone → tenant/customer/lead and either places an immediate outbound Superfone call or queues one.
```

Also update the `Directory Structure` code block to add the two new top-level webhook packages and `app/integrations/whatsapp/`:

```text
│   ├── integrations/
│   │   ├── superfone/         # SFVoPI (AI calls) + CRM (click-to-call)
│   │   └── whatsapp/          # Meta WhatsApp Cloud API client + per-tenant factory
│   ├── webhooks/
│   │   ├── superfone/         # SFVoPI + CRM event webhooks
│   │   ├── whatsapp/          # Tenant-scoped Meta WhatsApp webhook receiver
│   │   └── whatsapp_dashboard/ # Call-agent trigger for the dashboard product
```

(merge this into the existing tree rather than duplicating the whole block — find the existing `├── integrations/` and `├── webhooks/` lines and extend them in place).

- [ ] **Step 2: Full regression run**

Run:

```bash
cd backend
DATABASE_URL="postgresql+asyncpg://u:p@localhost/db" SUPABASE_URL="https://x.supabase.co" SUPABASE_SERVICE_ROLE_KEY="x" SUPABASE_JWT_SECRET="x" REDIS_URL="redis://localhost" python -c "import app.main"
pytest -q
ruff check app tests
mypy app
```

Expected: clean import, all tests pass, no lint/type errors. This is the definitive proof the app boots and the whole feature set works end to end at the unit-test level.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the multi-tenant direct-Meta WhatsApp integration"
```

---

## Post-plan manual step (not automatable, flag to the user)

Generating a real `WHATSAPP_CREDENTIALS_ENCRYPTION_KEY` and `WHATSAPP_DASHBOARD_CALL_AGENT_BEARER_SECRET`, provisioning a `.env` file, and configuring the `CALL_AGENT_API_URL`/`CALL_AGENT_API_KEY` environment variables on the (unmodified) `whatsapp_busness_dashboard` product to point at this backend's new `/webhooks/whatsapp-dashboard/call-agent` endpoint are deployment/ops actions outside this plan's scope — surface them to the user once implementation is complete rather than attempting them.
