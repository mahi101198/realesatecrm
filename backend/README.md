# Multi-Tenant Real Estate CRM Backend

Production-grade FastAPI backend for a multi-tenant real-estate CRM and AI voice sales platform.

## Features

### Phase 1 — Foundation

- **FastAPI Core**: Lifespan management, middleware stack, structured OpenAPI documentation.
- **Pydantic Settings**: Strongly typed environment configuration via `BaseSettings`.
- **Database Layer**: SQLAlchemy 2.x async engine + asyncpg connection pooling and clean session management.
- **Redis Connection**: Global async Redis connection with lifespan lifecycle management.
- **Structured Logging**: JSON formatted logs with automated sensitive key redaction (`Authorization`, tokens, keys, passwords).
- **Request Context**: Correlation/Request ID tracking (`X-Request-ID`) attached across logging and response headers.
- **Security & Error Handling**: Unified exception mapping, custom security middleware, headers, explicit CORS configuration.
- **Health Checks**:
  - `GET /api/v1/health`: Liveness probe (app running)
  - `GET /api/v1/ready`: Readiness probe (PostgreSQL & Redis availability)
- **Containerization**: Multi-stage Docker build with non-root security compliance (`appuser`).

### Phase 2 — Authentication, RBAC & Multi-Tenant Security

- **Supabase JWT verification**: signature, expiration, audience, subject; service_role bearer rejected.
- **DB-backed identity**: JWT `sub` → `users.auth_user_id` → roles → permissions → tenant.
- **RequestContext**: server-derived `user_id`, `tenant_id`, `role`, `permissions`, `is_super_admin`, `scope`.
- **Permission checks**: `require_permission("lead.read")` uses database permissions only (never JWT role claims).
- **Tenant isolation helpers**: `resolve_tenant_scope`, `ensure_tenant_resource_access` (cross-tenant → 404).
- **Current user**: `GET /api/v1/me`

See [docs/security-flow.md](docs/security-flow.md) for the authorization walkthrough.

### Phase 3 — CRM Domain APIs

Full repository/service/router stack per domain, all tenant-scoped and permission-gated:

- **Customers**: create, get, list (filter/paginate), update.
- **Leads**: create, get, list, update, notes (add/list), scoring trigger, delete, requirement history.
- **Projects**: get, list (filter/paginate).
- **Properties**: list (filter/paginate), get details, create.
- **Appointments**: create (site visits), get, list, update/reschedule, cancel — with double-booking/concurrency guards (`013_appointment_concurrency.sql`).
- **Follow-ups**: create, list, get, reschedule, cancel.

### Phase 4 — AI Voice Agent: Tooling & Call Orchestration

- **Pre-call context API**: `GET /api/v1/agent/context/{lead_id}` — deterministic snapshot (lead, customer, requirements, recent calls, open follow-ups) for the voice agent at call start.
- **20-tool AI tool registry** (`app/agent/tools/`): read tools (lead context, call summaries, follow-ups, property/project search & availability) and write tools (update lead requirements, update customer, add notes, record call observations, follow-up CRUD, site-visit scheduling CRUD, sales-agent transfer, callback creation) — all invoked through one authorized, idempotent, audited endpoint: `POST /api/v1/agent/tools/execute`.
- **Call-job lifecycle**: `create → prepare → start → complete`, with DNC re-checks, calling-window/concurrency enforcement, retry-on-outcome rules, and stuck-job reconciliation (`POST /api/v1/agent/calls/reconcile-stuck`).
- **Idempotency & audit**: write-tool calls are idempotency-keyed and logged to `activities` for traceability.
- **Hindi/English intent mapping**: conversational statement → tool-selection test coverage for the voice agent's dialogue layer.

---

## Directory Structure

```text
backend/
├── app/
│   ├── main.py               # FastAPI entrypoint & lifespan
│   ├── core/                 # Config, security, permissions, middleware
│   ├── auth/                 # JWT auth dependencies, /me router
│   ├── users/                # User/RBAC models, repository, service
│   ├── db/                   # Database session & transaction manager
│   ├── customers/            # Customer CRM domain
│   ├── leads/                # Lead CRM domain
│   ├── projects/             # Project domain
│   ├── properties/           # Property domain
│   ├── appointments/         # Site-visit appointment domain
│   ├── followups/            # Follow-up domain
│   ├── agent/                # AI voice agent: tools, orchestrator, gateway, router
│   └── shared/                # Shared types & utilities
├── docs/
│   └── security-flow.md
├── tests/                    # Unit, integration, security tests
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (optional for local containerized environment)
- PostgreSQL & Redis (or running via Docker Compose)

### Environment Setup

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Fill in required settings in `.env` (e.g. `DATABASE_URL`, `REDIS_URL`, `SUPABASE_JWT_SECRET`).

### Installation

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies (including dev)
pip install -e ".[dev]"
```

---

## Running Development Server

```bash
uvicorn app.main:app --reload --port 8000
```

Access:
- API Base: `http://localhost:8000/api/v1`
- Swagger UI Docs: `http://localhost:8000/docs`
- Health Endpoint: `http://localhost:8000/api/v1/health`
- Readiness Endpoint: `http://localhost:8000/api/v1/ready`
- Current User: `http://localhost:8000/api/v1/me` (requires Bearer token)

---

## Quality Assurance & Testing

```bash
# Run test suite
pytest

# Run linter
ruff check app tests

# Run type checker
mypy app
```

---

## Docker Support

### Build & Run Container

```bash
docker compose up --build
```

---

## Not Yet Implemented

- **Voice/telephony provider integration** — LiveKit and a telephony provider (e.g. Supaphone) are not wired up. What exists is the orchestration layer an external voice agent calls into (pre-call context, tool execution, call-job lifecycle) — not the live voice pipeline itself.
- **WhatsApp messaging** — DB schema is ready (`whatsapp_templates`, `whatsapp_messages`, `communication_logs` in `008_communication.sql`), but there's no `app/` module or router sending/receiving messages yet.
- **Role assign/remove and tenant CRUD admin APIs** — not yet exposed via HTTP.
- **Schema migrations tooling** — Supabase PostgreSQL migrations in `supabase/migrations/` remain hand-authored SQL; no migration framework (e.g. Alembic) is wired into the app.
