-- =============================================================================
-- MIGRATION 017: Index Corrections and Performance
--
-- Adds missing indexes identified in the audit. Does NOT add speculative
-- indexes — only indexes with a clear query pattern justification.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. agent_sessions: missing tenant-level index
--    FastAPI queries sessions by tenant for monitoring and cleanup.
-- ---------------------------------------------------------------------------

create index if not exists idx_agent_sessions_tenant
  on public.agent_sessions (tenant_id, created_at desc);

-- ---------------------------------------------------------------------------
-- 2. lead_score_events: covering index for score trend + call attribution
-- ---------------------------------------------------------------------------

create index if not exists idx_lead_score_events_lead_call
  on public.lead_score_events (lead_id, call_id, created_at desc)
  where call_id is not null;

-- ---------------------------------------------------------------------------
-- 3. calls: index by agent_config_id for config-version analytics
--    Answers: "How does call quality differ between agent config v1 and v2?"
-- ---------------------------------------------------------------------------

create index if not exists idx_calls_agent_config
  on public.calls (tenant_id, agent_config_id, created_at desc)
  where agent_config_id is not null;

-- ---------------------------------------------------------------------------
-- 4. appointments: tenant + date + status composite index
--    Used by daily schedule views and calendar queries.
-- ---------------------------------------------------------------------------

create index if not exists idx_appointments_tenant_date_status
  on public.appointments (tenant_id, scheduled_at, status);

-- ---------------------------------------------------------------------------
-- 5. properties: composite search index for AI property search tool
--    The AI calls search_properties() with: tenant + status + type + price.
--    This partial index covers available properties only (the common case).
-- ---------------------------------------------------------------------------

create index if not exists idx_properties_search_available
  on public.properties (tenant_id, property_type_id, base_price)
  where deleted_at is null and status = 'available';

-- ---------------------------------------------------------------------------
-- 6. ai_usage_events: call + service composite for cost view join
--    Used by v_call_cost_summary to aggregate per service type per call.
-- ---------------------------------------------------------------------------

create index if not exists idx_ai_usage_call_service
  on public.ai_usage_events (call_id, service_type)
  on conflict do nothing;

-- ---------------------------------------------------------------------------
-- 7. communication_logs: tenant + date for timeline view
-- ---------------------------------------------------------------------------

create index if not exists idx_comm_logs_tenant_date
  on public.communication_logs (tenant_id, created_at desc);

-- ---------------------------------------------------------------------------
-- 8. sales_assignments: active primary assignment per lead (frequent lookup)
--    "Who is the current agent for lead X?" is a very common query.
-- ---------------------------------------------------------------------------

create index if not exists idx_sales_assignments_active
  on public.sales_assignments (lead_id, is_primary, unassigned_at)
  where is_primary = true and unassigned_at is null;

-- ---------------------------------------------------------------------------
-- 9. campaign_leads: next_attempt_at for AI dialer scheduling
--    The AI dialer polls this index every cycle to find leads to call.
-- ---------------------------------------------------------------------------

create index if not exists idx_campaign_leads_next_attempt_scheduled
  on public.campaign_leads (tenant_id, next_attempt_at)
  where status in ('pending', 'callback')
    and next_attempt_at is not null;

-- ---------------------------------------------------------------------------
-- 10. customers: do_not_call filter for campaign/AI outbound protection
--     Every outbound call query should start with: AND do_not_call = false.
--     This partial index makes that filter fast.
-- ---------------------------------------------------------------------------

create index if not exists idx_customers_do_not_call
  on public.customers (tenant_id, do_not_call)
  where do_not_call = true and deleted_at is null;

-- ---------------------------------------------------------------------------
-- DOCUMENTATION: Future partitioning strategy for high-volume tables
-- ---------------------------------------------------------------------------

comment on table public.call_messages is
  'Individual utterances and tool calls during a call. Append-only. High volume. '
  'PARTITIONING STRATEGY (when > 50M rows): '
  'PARTITION BY RANGE (created_at) with monthly partitions. '
  'Queries always include call_id (leading to a specific call/month). '
  'Existing indexes (call_id, sequence_number) and (call_id, created_at) '
  'remain valid on each partition. '
  'Migration path: pg_partman or manual CREATE TABLE ... PARTITION OF.';

comment on table public.call_events is
  'Technical lifecycle events for a call. Append-only. High volume. '
  'PARTITIONING STRATEGY (when > 100M rows): '
  'PARTITION BY RANGE (created_at) with monthly partitions. '
  'Same strategy as call_messages.';

comment on table public.ai_usage_events is
  'Granular AI cost events. Append-only. '
  'PARTITIONING STRATEGY (when > 50M rows): '
  'PARTITION BY RANGE (created_at) with monthly partitions. '
  'Or PARTITION BY HASH (tenant_id) for even distribution across large tenants.';
