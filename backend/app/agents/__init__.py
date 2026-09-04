"""AI agent layer: LangGraph lead-workflow orchestrator + WhatsApp agent.

WHY A SEPARATE PACKAGE FROM `app/agent/` (singular)
---------------------------------------------------
`app/agent/` is the *voice/CRM tool* layer: the tool registry, the call
orchestrator, the handoff service. It is deterministic plumbing that predates
any LLM in this codebase and is shared by the HTTP API, the background call
scheduler and the webhooks.

`app/agents/` (plural) is the *reasoning* layer added on top: it decides what
to do about an inbound message and then calls into `app/agent/` to actually do
it. The dependency arrow only ever points this way -- nothing under
`app/agent/` imports from `app/agents/`.

HARD RULES FOR EVERYTHING IN THIS PACKAGE
-----------------------------------------
1. No raw SQL against tenant tables. Every read and write goes through an
   existing tenant-scoped service, resolver or registered tool.
2. Every LLM call goes through `app.agents.llm`. That module is the single
   seam the test suite mocks, and the single place the `google-genai` SDK is
   imported.
3. The AI layer fails CLOSED and fails QUIET: an LLM outage, a bad key or a
   malformed model response must never corrupt CRM state or break the caller
   (see spec section 20).
"""
