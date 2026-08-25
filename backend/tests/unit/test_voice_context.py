"""Voice layer: correlating a media stream to a call, and building the briefing.

The briefing is the spec-section-4 contract (tenant_id, contact_id, lead_id,
customer_name, interest, budget, conversation_summary, reason_for_call). These
tests pin that contract and, just as importantly, pin that a MISSING piece
degrades to None instead of raising -- a briefing that throws would drop a live
call.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.voice.context import (
    CallCorrelation,
    VoiceCallContext,
    build_voice_call_context,
    load_conversation_summary,
    reason_for_job_type,
    resolve_call_by_provider_id,
)


def _session_returning(*rows) -> AsyncMock:
    """A session whose successive `.execute()` calls yield the given rows.

    `.execute()` must return a plain MagicMock: on an unspecced AsyncMock the
    chained `.mappings().one_or_none()` would itself be awaitable and blow up
    with "coroutine has no attribute".
    """
    session = AsyncMock()
    results = []
    for row in rows:
        result = MagicMock()
        result.mappings.return_value.one_or_none.return_value = row
        results.append(result)
    session.execute.side_effect = results
    return session


def _correlation(**overrides) -> CallCorrelation:
    base = {
        "tenant_id": uuid4(),
        "call_id": uuid4(),
        "call_job_id": uuid4(),
        "call_attempt_id": uuid4(),
        "contact_id": uuid4(),
        "lead_id": uuid4(),
        "provider_call_id": "req-123",
    }
    base.update(overrides)
    return CallCorrelation(**base)


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


async def test_resolve_returns_the_full_crm_triple() -> None:
    ids = {k: uuid4() for k in ("tenant", "call", "job", "attempt", "contact", "lead")}
    session = _session_returning(
        {
            "tenant_id": ids["tenant"],
            "call_id": ids["call"],
            "call_job_id": ids["job"],
            "call_attempt_id": ids["attempt"],
            "contact_id": ids["contact"],
            "lead_id": ids["lead"],
            "job_type": "callback",
        }
    )

    resolved = await resolve_call_by_provider_id(session, "req-123")
    assert resolved is not None
    correlation, extras = resolved

    assert correlation.tenant_id == ids["tenant"]
    assert correlation.call_job_id == ids["job"]
    assert correlation.call_attempt_id == ids["attempt"]
    assert correlation.provider_call_id == "req-123"
    assert extras["job_type"] == "callback"


async def test_resolve_returns_none_for_a_call_we_never_placed() -> None:
    """An unknown provider id means 'close the socket', never an exception."""
    session = _session_returning(None)
    assert await resolve_call_by_provider_id(session, "not-ours") is None


async def test_resolve_is_keyed_only_on_the_provider_call_id() -> None:
    """Tenant is the RESULT of this lookup, so it must not be a bind parameter."""
    session = _session_returning(None)
    await resolve_call_by_provider_id(session, "req-9")
    _, params = session.execute.call_args[0]
    assert params == {"provider_call_id": "req-9"}


# ---------------------------------------------------------------------------
# reason_for_call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job_type", "fragment"),
    [
        ("initial_lead_call", "First call"),
        ("follow_up_call", "Follow-up"),
        ("callback", "called back"),
        ("human_callback", "human callback"),
    ],
)
def test_known_job_types_render_a_spoken_reason(job_type: str, fragment: str) -> None:
    reason = reason_for_job_type(job_type)
    assert reason is not None
    assert fragment.lower() in reason.lower()


def test_unknown_job_type_still_renders_something_usable() -> None:
    assert "surprise_call" in (reason_for_job_type("surprise_call") or "")


def test_absent_job_type_is_none_not_a_guess() -> None:
    assert reason_for_job_type(None) is None


# ---------------------------------------------------------------------------
# Conversation summary
# ---------------------------------------------------------------------------


async def test_summary_prefers_conversation_summaries_table() -> None:
    session = _session_returning({"summary_text": "Wants a 3BHK in Whitefield."})
    summary = await load_conversation_summary(
        session, tenant_id=uuid4(), contact_id=uuid4(), lead_id=uuid4()
    )
    assert summary == "Wants a 3BHK in Whitefield."
    assert session.execute.await_count == 1


async def test_summary_falls_back_to_the_rolling_agent_session_summary() -> None:
    session = _session_returning(None, {"conversation_summary": "Asked about EMI."})
    summary = await load_conversation_summary(
        session, tenant_id=uuid4(), contact_id=uuid4(), lead_id=None
    )
    assert summary == "Asked about EMI."


async def test_no_history_yields_none_not_an_error() -> None:
    session = _session_returning(None, None)
    assert (
        await load_conversation_summary(
            session, tenant_id=uuid4(), contact_id=uuid4(), lead_id=uuid4()
        )
        is None
    )


# ---------------------------------------------------------------------------
# The briefing itself
# ---------------------------------------------------------------------------


async def test_briefing_carries_every_spec_section_4_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    correlation = _correlation()
    snapshot = MagicMock()
    snapshot.model_dump.return_value = {
        "customer": {"full_name": "Asha Rao", "preferred_language": "en"},
        "requirement": {
            "bedrooms": 3,
            "property_type": "Apartment",
            "preferred_location": "Whitefield, Bengaluru",
            "budget_min": "8000000",
            "budget_max": "10000000",
        },
        "property_interests": [{"project_name": "Palm Grove"}],
    }
    repo = MagicMock()
    repo.get_pre_call_context = AsyncMock(return_value=snapshot)
    monkeypatch.setattr("app.voice.context.AgentRepository", lambda _s: repo)

    session = _session_returning({"summary_text": "Chatted on WhatsApp about Palm Grove."})
    context = await build_voice_call_context(
        session, correlation, reason_for_call="The customer asked to be called."
    )

    assert context.tenant_id == correlation.tenant_id
    assert context.contact_id == correlation.contact_id
    assert context.lead_id == correlation.lead_id
    assert context.customer_name == "Asha Rao"
    assert context.preferred_language == "en"
    assert "3 BHK" in (context.interest or "")
    assert "Whitefield" in (context.interest or "")
    assert "Palm Grove" in (context.interest or "")
    assert context.budget == "8000000 to 10000000"
    assert context.conversation_summary == "Chatted on WhatsApp about Palm Grove."
    assert context.reason_for_call == "The customer asked to be called."


async def test_briefing_survives_a_lead_with_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No snapshot is a thinner agent, not a dropped call."""
    repo = MagicMock()
    repo.get_pre_call_context = AsyncMock(return_value=None)
    monkeypatch.setattr("app.voice.context.AgentRepository", lambda _s: repo)

    session = _session_returning(None, None)
    context = await build_voice_call_context(session, _correlation())

    assert context.customer_name is None
    assert context.interest is None
    assert context.budget is None
    assert context.conversation_summary is None
    assert context.preferred_language == "hi"


async def test_briefing_skips_the_snapshot_read_entirely_without_a_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = MagicMock()
    repo.get_pre_call_context = AsyncMock(return_value=None)
    monkeypatch.setattr("app.voice.context.AgentRepository", lambda _s: repo)

    session = _session_returning(None, None)
    await build_voice_call_context(session, _correlation(lead_id=None))
    repo.get_pre_call_context.assert_not_awaited()


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ({"budget_min": "1", "budget_max": "2"}, "1 to 2"),
        ({"budget_max": "2"}, "up to 2"),
        ({"budget_min": "1"}, "from 1"),
        ({}, None),
    ],
)
async def test_budget_is_rendered_as_a_speakable_phrase(
    monkeypatch: pytest.MonkeyPatch, requirement: dict, expected: str | None
) -> None:
    snapshot = MagicMock()
    snapshot.model_dump.return_value = {"customer": {}, "requirement": requirement}
    repo = MagicMock()
    repo.get_pre_call_context = AsyncMock(return_value=snapshot)
    monkeypatch.setattr("app.voice.context.AgentRepository", lambda _s: repo)

    session = _session_returning(None, None)
    context = await build_voice_call_context(session, _correlation())
    assert context.budget == expected


# ---------------------------------------------------------------------------
# Room metadata
# ---------------------------------------------------------------------------


def test_room_metadata_carries_ids_and_no_personal_data() -> None:
    """Room metadata is visible to every participant token and to LiveKit's
    dashboard, so it must be correlation ids only."""
    context = VoiceCallContext(
        tenant_id=uuid4(),
        contact_id=uuid4(),
        lead_id=uuid4(),
        call_id=uuid4(),
        call_job_id=uuid4(),
        call_attempt_id=uuid4(),
        customer_name="Asha Rao",
        conversation_summary="Discussed budget of 1 crore.",
        lead_snapshot={"customer": {"phone": "+919999999999"}},
    )
    metadata = context.as_room_metadata()

    assert set(metadata) == {"tenant_id", "call_job_id", "call_attempt_id", "lead_id"}
    blob = " ".join(metadata.values())
    assert "Asha Rao" not in blob
    assert "+919999999999" not in blob
    assert "crore" not in blob


def test_room_metadata_values_are_all_strings() -> None:
    """It round-trips through JSON into LiveKit; non-strings would not survive."""
    context = VoiceCallContext(
        tenant_id=uuid4(),
        contact_id=uuid4(),
        lead_id=None,
        call_id=uuid4(),
        call_job_id=uuid4(),
        call_attempt_id=uuid4(),
    )
    assert all(isinstance(v, str) for v in context.as_room_metadata().values())
    assert context.as_room_metadata()["lead_id"] == ""
