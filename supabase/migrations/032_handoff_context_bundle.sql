-- =============================================================================
-- MIGRATION 032: Human-Handoff Context Bundle
--
-- When an AI agent escalates to a human, the human should not have to re-ask
-- everything the AI already knows. These three ADDITIVE, NULLABLE columns
-- capture that briefing on public.sales_handoffs.
--
-- REAL NOW (deterministic, populated at handoff-creation time by
-- AgentRepository.build_handoff_context_bundle):
--   * context_snapshot   -- contact facts + current lead facts (budget,
--                           interest level, stage, requirements) read straight
--                           off the customers/leads rows, tenant-scoped.
--   * prior_ai_actions   -- denormalized newline-separated digest of the most
--                           recent public.activities rows for this lead
--                           (the AI tool-execution audit trail the existing
--                           dispatch_agent_tool already writes).
--
-- PLACEHOLDER FOR PHASE 2 (left NULL by this phase; an AI summarizer fills it):
--   * conversation_summary -- natural-language recap of the conversation.
--
-- `reason` (why the transfer was requested) already exists on this table since
-- migration 021 and is unchanged.
-- =============================================================================

alter table public.sales_handoffs
  add column if not exists context_snapshot jsonb not null default '{}';

alter table public.sales_handoffs
  add column if not exists prior_ai_actions text;

alter table public.sales_handoffs
  add column if not exists conversation_summary text;

comment on column public.sales_handoffs.context_snapshot is
  'REAL NOW. Deterministic tenant-scoped snapshot of the contact and the '
  'current lead (budget/interest/stage/requirements) taken when the handoff was '
  'requested. Built by AgentRepository.build_handoff_context_bundle.';

comment on column public.sales_handoffs.prior_ai_actions is
  'REAL NOW. Denormalized digest of recent public.activities rows for this '
  'lead -- the actions the AI already took, so the human does not repeat them.';

comment on column public.sales_handoffs.conversation_summary is
  'PLACEHOLDER for phase 2. Left NULL by the deterministic foundation layer; '
  'an AI summarizer will populate it. Never read as authoritative until then.';
