"""Voice Agent: tool-allowlist enforcement, failure posture, outcome recording.

Mirrors the WhatsApp agent's test approach -- patch `app.voice.llm` and assert
on the deterministic machinery around it. Nothing here talks to LiveKit
Inference, LiveKit rooms or a database.

The three properties that matter most and are all pinned below:
  1. the agent CANNOT reach a write tool (spec sections 3/17);
  2. an AI failure never propagates into a live call, and never leaves CRM
     state unwritten (spec section 20);
  3. the call lifecycle is changed in exactly ONE place --
     `AgentGateway.record_call_completed` -- and this package never re-implements
     the retry policy or the job state machine.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agents.security import AI_READ_PERMISSIONS
from app.core.permissions import Permission
from app.core.request_context import SecurityScope
from app.voice import llm as voice_llm
from app.voice.agent import DEFAULT_OUTCOME, FALLBACK_REPLY, VoiceAgent
from app.voice.context import VoiceCallContext
from app.voice.prompts import ALLOWED_TOOL_NAMES, MAX_SPOKEN_REPLY_CHARS


def _context(**overrides) -> VoiceCallContext:
    base = {
        "tenant_id": uuid4(),
        "contact_id": uuid4(),
        "lead_id": uuid4(),
        "call_id": uuid4(),
        "call_job_id": uuid4(),
        "call_attempt_id": uuid4(),
        "customer_name": "Asha Rao",
        "interest": "3 BHK in Whitefield",
        "budget": "80L to 1Cr",
    }
    base.update(overrides)
    return VoiceCallContext(**base)


def _agent(**context_overrides) -> VoiceAgent:
    """A VoiceAgent with every collaborator replaced by a mock.

    `session.execute` is never reached because the repository is mocked, which
    is the point: these tests are about the agent's decisions, not its SQL.
    """
    agent = VoiceAgent(AsyncMock(), _context(**context_overrides))
    agent.repository = AsyncMock()
    agent.gateway = AsyncMock()
    agent.gateway.record_call_completed.return_value = {"next_status": "completed"}
    agent.handoffs = AsyncMock()
    agent.handoffs.request_handoff.return_value = {"id": uuid4()}
    return agent


# ---------------------------------------------------------------------------
# Security: the read-only, allowlisted tool surface
# ---------------------------------------------------------------------------


def test_agent_runs_on_a_tenant_scoped_read_only_context() -> None:
    agent = _agent()
    ctx = agent._read_context

    assert ctx.scope == SecurityScope.TENANT
    assert ctx.tenant_id == agent.context.tenant_id
    assert ctx.is_super_admin is False
    assert ctx.permissions == AI_READ_PERMISSIONS


@pytest.mark.parametrize(
    "write_permission",
    [
        Permission.LEAD_UPDATE,
        Permission.APPOINTMENT_CREATE,
        Permission.LEAD_ASSIGN,
    ],
)
def test_agent_context_holds_no_write_permission(write_permission: Permission) -> None:
    """The voice agent must not own CRM state (spec section 4)."""
    assert write_permission.value not in _agent()._read_context.permissions


@pytest.mark.parametrize(
    "tool_name",
    [
        "schedule_site_visit",
        "update_lead_requirements",
        "make_instant_call",
        "add_lead_note",
        "update_lead_score",
        "request_sales_agent_transfer",
        "definitely_not_a_tool",
    ],
)
async def test_non_allowlisted_tools_are_refused_before_the_registry(
    tool_name: str,
) -> None:
    """The model picks the name, so the name is untrusted input."""
    result = await _agent()._execute_tool(tool_name, {})
    assert result["success"] is False
    assert result["error_code"] == "TOOL_NOT_ALLOWED"


def test_the_allowlist_is_exactly_the_four_read_lookups() -> None:
    assert sorted(ALLOWED_TOOL_NAMES) == [
        "get_project_details",
        "get_property_availability",
        "get_property_details",
        "search_properties",
    ]


def test_every_allowlisted_tool_is_a_registered_read_tool() -> None:
    """A name on the allowlist that is not classified READ would be a hole."""
    from app.agent.tools import READ_TOOLS, TOOL_REGISTRY

    for name in ALLOWED_TOOL_NAMES:
        assert name in TOOL_REGISTRY
        assert name in READ_TOOLS


async def test_allowlisted_tool_reaches_the_registry_with_the_read_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.tools import TOOL_REGISTRY

    handler = AsyncMock(return_value={"success": True, "units": []})
    monkeypatch.setitem(TOOL_REGISTRY, "search_properties", handler)

    agent = _agent()
    result = await agent._execute_tool("search_properties", {"budget_max": 5000000})

    assert result["success"] is True
    passed_context = handler.await_args[0][0]
    assert passed_context is agent._read_context


async def test_string_uuids_from_the_model_are_coerced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.tools import TOOL_REGISTRY

    handler = AsyncMock(return_value={"success": True})
    monkeypatch.setitem(TOOL_REGISTRY, "get_property_details", handler)

    property_id = uuid4()
    await _agent()._execute_tool("get_property_details", {"property_id": str(property_id)})
    assert handler.await_args.kwargs["property_id"] == property_id


async def test_malformed_uuids_are_dropped_not_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.tools import TOOL_REGISTRY

    handler = AsyncMock(return_value={"success": True})
    monkeypatch.setitem(TOOL_REGISTRY, "get_property_details", handler)

    await _agent()._execute_tool("get_property_details", {"property_id": "not-a-uuid"})
    assert "property_id" not in handler.await_args.kwargs


# ---------------------------------------------------------------------------
# Turn taking and failure posture
# ---------------------------------------------------------------------------


async def test_respond_returns_the_models_line_and_records_both_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(voice_llm, "call_with_tools", AsyncMock(return_value="  Sure, 3BHK.  "))
    agent = _agent()

    spoken = await agent.respond("Do you have a 3BHK?")

    assert spoken == "Sure, 3BHK."
    assert [t["speaker"] for t in agent.transcript] == ["customer", "agent"]
    assert agent.transcript[0]["text"] == "Do you have a 3BHK?"
    assert agent.repository.append_message.await_count == 2


async def test_spoken_replies_are_trimmed_to_a_speakable_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wall of text read aloud is an announcement, not a conversation."""
    monkeypatch.setattr(voice_llm, "call_with_tools", AsyncMock(return_value="x" * 5000))
    spoken = await _agent().respond("hello")
    assert len(spoken) == MAX_SPOKEN_REPLY_CHARS


async def test_llm_failure_mid_call_degrades_to_a_recoverable_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception here would be dead air on a live customer call."""
    monkeypatch.setattr(
        voice_llm,
        "call_with_tools",
        AsyncMock(side_effect=voice_llm.VoiceLLMUnavailableError("no key")),
    )
    agent = _agent()
    assert await agent.respond("hello?") == FALLBACK_REPLY
    # ...and the failure is remembered, so the call ends with a human, not a guess.
    assert agent.llm_degraded is True


async def test_any_escaping_exception_is_contained_not_just_llm_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`respond` runs inside the livekit session task; anything that escapes it
    kills the media stream, not just the turn."""
    monkeypatch.setattr(
        voice_llm, "call_with_tools", AsyncMock(side_effect=RuntimeError("SDK changed shape"))
    )
    agent = _agent()
    assert await agent.respond("hello?") == FALLBACK_REPLY
    assert agent.llm_degraded is True


async def test_opening_falls_back_to_a_deterministic_greeting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        voice_llm,
        "call_with_tools",
        AsyncMock(side_effect=voice_llm.VoiceLLMResponseError("bad")),
    )
    agent = _agent()
    spoken = await agent.open()
    assert "Asha Rao" in spoken
    assert agent.llm_degraded is True


async def test_a_transcript_write_failure_never_breaks_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing a transcript row is regrettable; dropping the call is not allowed."""
    monkeypatch.setattr(voice_llm, "call_with_tools", AsyncMock(return_value="Yes."))
    agent = _agent()
    agent.repository.append_message.side_effect = RuntimeError("db down")

    assert await agent.respond("hi") == "Yes."


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------


async def test_summarise_returns_the_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        voice_llm,
        "call_structured",
        AsyncMock(
            return_value={
                "outcome": "site_visit_scheduled",
                "summary": "Wants to visit on Saturday.",
                "customer_intent": "book_visit",
                "human_transfer_required": False,
                "callback_requested": False,
                "objections": [],
                "next_best_action": "Confirm the slot.",
            }
        ),
    )
    agent = _agent()
    agent.transcript = [{"speaker": "customer", "text": "Can I visit?"}]

    result = await agent.summarise()
    assert result["outcome"] == "site_visit_scheduled"
    assert result["degraded"] is False


async def test_summarise_degrades_to_connected_and_asks_for_a_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'connected' is the honest floor for the enum -- we cannot claim the customer
    wanted anything. But an AI failure is never a licence to guess, so the call
    is handed to a person rather than filed with no summary and no follow-up."""
    monkeypatch.setattr(
        voice_llm,
        "call_structured",
        AsyncMock(side_effect=voice_llm.VoiceLLMUnavailableError("no key")),
    )
    agent = _agent()
    agent.transcript = [{"speaker": "customer", "text": "hello"}]

    result = await agent.summarise()
    assert result["outcome"] == DEFAULT_OUTCOME
    assert result["degraded"] is True
    assert result["human_transfer_required"] is True


async def test_a_mid_call_failure_forces_a_handoff_even_if_the_summary_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary is real, but the conversation the customer had was not the
    one we meant to have. A person picks it up regardless."""
    monkeypatch.setattr(
        voice_llm,
        "call_structured",
        AsyncMock(
            return_value={
                "outcome": "connected",
                "summary": "Discussed options.",
                "human_transfer_required": False,
            }
        ),
    )
    agent = _agent()
    agent.llm_degraded = True
    agent.transcript = [{"speaker": "customer", "text": "hello"}]

    result = await agent.summarise()
    assert result["degraded"] is False
    assert result["human_transfer_required"] is True


async def test_summarise_on_an_empty_call_never_calls_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structured = AsyncMock()
    monkeypatch.setattr(voice_llm, "call_structured", structured)
    result = await _agent().summarise()
    structured.assert_not_awaited()
    assert result["outcome"] == DEFAULT_OUTCOME
    # A silent-but-healthy call is not a failure and must not page a human.
    assert result["human_transfer_required"] is False


async def test_an_off_enum_outcome_is_clamped_before_it_reaches_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`call_attempt_outcome` is a DB enum; an unknown value would be a 22P02."""
    monkeypatch.setattr(
        voice_llm,
        "call_structured",
        AsyncMock(return_value={"outcome": "customer_was_delighted", "summary": "n/a"}),
    )
    agent = _agent()
    agent.transcript = [{"speaker": "customer", "text": "hi"}]
    assert (await agent.summarise())["outcome"] == DEFAULT_OUTCOME


# ---------------------------------------------------------------------------
# Outcome recording -- the single lifecycle seam
# ---------------------------------------------------------------------------


async def test_outcome_flows_through_the_gateway_and_nowhere_else() -> None:
    agent = _agent()
    result = {
        "outcome": "site_visit_scheduled",
        "summary": "Visiting Saturday.",
        "customer_intent": "book_visit",
        "human_transfer_required": False,
        "callback_requested": False,
        "objections": ["price"],
        "next_best_action": "Confirm slot.",
    }

    recorded = await agent.record_outcome(result, duration_seconds=95)

    agent.gateway.record_call_completed.assert_awaited_once()
    kwargs = agent.gateway.record_call_completed.await_args.kwargs
    assert kwargs["tenant_id"] == agent.context.tenant_id
    assert kwargs["call_job_id"] == agent.context.call_job_id
    assert kwargs["call_attempt_id"] == agent.context.call_attempt_id
    assert kwargs["outcome"] == "site_visit_scheduled"
    assert kwargs["duration_seconds"] == 95
    assert kwargs["call_summary"] == "Visiting Saturday."
    assert recorded["next_status"] == "completed"


async def test_outcome_writes_the_existing_006_tables_only() -> None:
    agent = _agent()
    await agent.record_outcome({"outcome": "connected", "summary": "Chatted."})

    agent.repository.upsert_agent_session.assert_awaited_once()
    agent.repository.upsert_conversation_summary.assert_awaited_once()
    summary_kwargs = agent.repository.upsert_conversation_summary.await_args.kwargs
    assert summary_kwargs["tenant_id"] == agent.context.tenant_id
    assert summary_kwargs["call_id"] == agent.context.call_id
    assert summary_kwargs["summary_text"] == "Chatted."


async def test_human_transfer_uses_the_one_existing_handoff_service() -> None:
    """No second escalation mechanism -- `request_handoff` or nothing."""
    agent = _agent()
    recorded = await agent.record_outcome(
        {
            "outcome": "connected",
            "summary": "Wants to negotiate.",
            "human_transfer_required": True,
            "next_best_action": "Sales manager to call.",
        }
    )

    agent.handoffs.request_handoff.assert_awaited_once()
    args = agent.handoffs.request_handoff.await_args.args
    assert args[0] == agent.context.tenant_id
    assert args[1] == agent.context.lead_id
    assert args[2] == agent.context.contact_id
    # A requested transfer IS the outcome, and it is terminal for the retry policy.
    assert recorded["outcome"] == "human_transfer"
    assert agent.gateway.record_call_completed.await_args.kwargs["outcome"] == "human_transfer"


async def test_no_handoff_is_requested_without_a_lead() -> None:
    """`request_handoff` needs a lead; a leadless call must not fabricate one."""
    agent = _agent(lead_id=None)
    await agent.record_outcome({"outcome": "connected", "human_transfer_required": True})
    agent.handoffs.request_handoff.assert_not_awaited()


async def test_a_callback_request_publishes_an_event_rather_than_calling_an_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec section 17: no agent-to-agent calls. `public.events` is the seam."""
    published = AsyncMock()
    monkeypatch.setattr("app.voice.agent.publish_event", published)

    agent = _agent()
    await agent.record_outcome(
        {"outcome": "customer_requested_callback", "callback_requested": True}
    )

    published.assert_awaited_once()
    payload = published.await_args.kwargs["payload"]
    assert payload["source"] == "voice_agent"
    assert published.await_args.kwargs["tenant_id"] == agent.context.tenant_id


async def test_no_event_is_published_when_no_callback_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = AsyncMock()
    monkeypatch.setattr("app.voice.agent.publish_event", published)
    await _agent().record_outcome({"outcome": "connected", "callback_requested": False})
    published.assert_not_awaited()


async def test_a_degraded_summary_still_records_a_valid_outcome() -> None:
    """AI failure must never leave the call job unrecorded (spec section 20)."""
    agent = _agent()
    recorded = await agent.record_outcome(
        {"outcome": DEFAULT_OUTCOME, "summary": None, "degraded": True}
    )

    assert recorded["outcome"] == DEFAULT_OUTCOME
    agent.gateway.record_call_completed.assert_awaited_once()
    # No model produced this, so nothing may claim a model did.
    assert (
        agent.repository.upsert_conversation_summary.await_args.kwargs["generated_by_model"]
        is None
    )


async def test_duration_is_derived_when_the_caller_does_not_supply_one() -> None:
    agent = _agent()
    await agent.record_outcome({"outcome": "connected"})
    assert agent.gateway.record_call_completed.await_args.kwargs["duration_seconds"] >= 0
