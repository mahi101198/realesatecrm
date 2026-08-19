-- =============================================================================
-- MIGRATION 022: Call Lifecycle Hardening & Idempotency
-- operation_idempotency_keys + concurrency locking & query indexes
-- =============================================================================

-- ---------------------------------------------------------------------------
-- OPERATION_IDEMPOTENCY_KEYS
-- ---------------------------------------------------------------------------
-- Idempotency table for preventing duplicate side effects across tool calls
-- and API retries (create_follow_up, schedule_site_visit, handoffs, calls).
-- ---------------------------------------------------------------------------

create table if not exists public.operation_idempotency_keys (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete restrict,
  idempotency_key       text not null,
  operation_type        text not null,
  response_payload      jsonb not null,
  created_at            timestamptz not null default now(),

  constraint uq_operation_idempotency unique (tenant_id, idempotency_key, operation_type)
);

create index if not exists idx_operation_idempotency_tenant_key
  on public.operation_idempotency_keys (tenant_id, idempotency_key);

-- ---------------------------------------------------------------------------
-- INDEXES FOR CONCURRENCY LOCKING & STUCK JOB RECOVERY
-- ---------------------------------------------------------------------------

create index if not exists idx_call_jobs_lock_claim
  on public.call_jobs (tenant_id, status, priority, scheduled_at)
  where status in ('queued', 'retry_pending', 'ready');

create index if not exists idx_call_jobs_stuck_recovery
  on public.call_jobs (status, updated_at)
  where status in ('preparing', 'ready', 'calling');

create index if not exists idx_call_attempts_job_number
  on public.call_attempts (call_job_id, attempt_number);
