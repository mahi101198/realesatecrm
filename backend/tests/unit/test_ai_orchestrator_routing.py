"""Unit tests for the orchestrator's deterministic decision table.

`determine_next_action` is a pure function -- no session, no model, no I/O --
which is exactly why the routing policy lives there. These tests pin the
policy, including the ordering that makes escalation win over everything else.
"""

from uuid import uuid4

from app.agents.orchestrator.nodes import determine_next_action
from app.agents.orchestrator.state import Intent, NextAction, new_state


def _state(**overrides):
    state = new_state(
        tenant_id=uuid4(),
        contact_id=uuid4(),
        lead_id=uuid4(),
        conversation_id=uuid4(),
        latest_message="hi there",
    )
    qualification = overrides.pop("qualification", None)
    state["qualification"] = qualification if qualification is not None else {
        "name": "Jane",
        "budget": "5000000 - 7000000",
        "interest": "3BHK",
        "call_availability": None,
        "missing": [],
        "is_qualified": True,
    }
    state["confidence"] = overrides.pop("confidence", 0.9)
    state["intent"] = overrides.pop("intent", Intent.GENERAL_INQUIRY.value)
    extracted = overrides.pop("extracted", {})
    state["context"] = {"extracted": extracted}
    state.update(overrides)
    return state


def _action(state) -> str:
    return determine_next_action(state)["next_action"]


def test_empty_message_waits() -> None:
    """Nothing to answer means nothing to do -- checked before anything else,
    so a media-only or blank delivery never triggers an escalation."""
    assert _action(_state(latest_message="   ")) == NextAction.WAIT.value


def test_ai_failure_routes_to_a_human() -> None:
    """When the classifier failed we did not understand the buyer, so guessing
    an action would be worse than fetching a person."""
    assert _action(_state(error="intent_classification_failed: boom")) == (
        NextAction.TRANSFER_HUMAN.value
    )


def test_low_confidence_routes_to_a_human() -> None:
    """Spec section 12: below the confidence floor the AI does not act."""
    assert _action(_state(confidence=0.2)) == NextAction.TRANSFER_HUMAN.value


def test_confidence_at_the_floor_is_actionable() -> None:
    """The floor is inclusive -- a boundary test so a future tweak to
    MIN_ACTIONABLE_CONFIDENCE cannot silently flip the comparison."""
    from app.agents.orchestrator.state import MIN_ACTIONABLE_CONFIDENCE

    state = _state(
        confidence=MIN_ACTIONABLE_CONFIDENCE,
        intent=Intent.PROPERTY_QUESTION.value,
    )
    assert _action(state) == NextAction.SEND_PROPERTY_DETAILS.value


def test_model_flagged_requires_human_wins() -> None:
    """The extraction's own `requires_human` flag escalates even when the
    classified intent looks benign and confidence is high."""
    state = _state(
        intent=Intent.GENERAL_INQUIRY.value,
        confidence=0.99,
        extracted={"requires_human": True, "human_reason": "angry"},
    )
    assert _action(state) == NextAction.TRANSFER_HUMAN.value


def test_explicit_human_request_intent_escalates() -> None:
    state = _state(intent=Intent.HUMAN_REQUEST.value, latest_message="hello")
    assert _action(state) == NextAction.TRANSFER_HUMAN.value


def test_keyword_escalation_overrides_a_confident_benign_classification() -> None:
    """The keyword net is a rules-first backstop: negotiation, complaints and
    legal/regulatory language go to a person no matter what the model said."""
    for message in (
        "can you give me a discount on this",
        "I want to file a complaint",
        "my lawyer says the RERA registration is wrong",
        "please connect me to a real person",
    ):
        state = _state(
            latest_message=message,
            intent=Intent.GENERAL_INQUIRY.value,
            confidence=0.99,
        )
        assert _action(state) == NextAction.TRANSFER_HUMAN.value, message


def test_keyword_matching_respects_word_boundaries() -> None:
    """`legal` must not fire on `legalese`; otherwise ordinary sentences get
    escalated and the AI layer does nothing useful."""
    state = _state(
        latest_message="skip the legalese and show me flats",
        intent=Intent.PROPERTY_QUESTION.value,
    )
    assert _action(state) == NextAction.SEND_PROPERTY_DETAILS.value


def test_direct_intents_map_one_to_one() -> None:
    """Explicit intents beat qualification bookkeeping: never answer
    'call me now' with 'what is your budget?'."""
    unqualified = {
        "name": None,
        "budget": None,
        "interest": None,
        "call_availability": None,
        "missing": ["name", "budget", "interest", "call_availability"],
        "is_qualified": False,
    }
    cases = {
        Intent.NOT_INTERESTED.value: NextAction.MARK_NOT_INTERESTED.value,
        Intent.CALL_NOW.value: NextAction.CALL_NOW.value,
        Intent.CALL_LATER.value: NextAction.CALL_LATER.value,
        Intent.VISIT_REQUEST.value: NextAction.SCHEDULE_VISIT.value,
        Intent.PROPERTY_QUESTION.value: NextAction.SEND_PROPERTY_DETAILS.value,
    }
    for intent, expected in cases.items():
        state = _state(intent=intent, qualification=dict(unqualified), latest_message="ok")
        assert _action(state) == expected, intent


def test_missing_qualification_asks_for_information() -> None:
    state = _state(
        intent=Intent.PROVIDE_QUALIFICATION_INFO.value,
        qualification={
            "name": "Jane",
            "budget": None,
            "interest": None,
            "call_availability": None,
            "missing": ["budget", "interest", "call_availability"],
            "is_qualified": False,
        },
    )
    assert _action(state) == NextAction.ASK_INFORMATION.value


def test_fully_qualified_with_availability_queues_a_call() -> None:
    """Once every qualification field is on file and we know when to ring,
    the conversation belongs on the phone channel."""
    state = _state(
        intent=Intent.PROVIDE_QUALIFICATION_INFO.value,
        qualification={
            "name": "Jane",
            "budget": "5000000 - 7000000",
            "interest": "3BHK",
            "call_availability": "weekday evenings",
            "missing": [],
            "is_qualified": True,
        },
    )
    assert _action(state) == NextAction.CALL_LATER.value


def test_fully_qualified_general_inquiry_replies_with_details() -> None:
    assert _action(_state(intent=Intent.GENERAL_INQUIRY.value)) == (
        NextAction.SEND_PROPERTY_DETAILS.value
    )


def test_qualified_but_unclassifiable_message_falls_back_to_follow_up() -> None:
    """`other` with nothing missing and no stated availability: keep the lead
    warm rather than inventing an action."""
    assert _action(_state(intent=Intent.OTHER.value)) == NextAction.FOLLOW_UP.value


def test_every_action_has_an_executor_node() -> None:
    """Guards against adding a NextAction member without wiring a node, which
    would make `route_next_action` silently fall through to WAIT."""
    from app.agents.orchestrator.nodes import ACTION_TO_NODE, EXECUTORS

    assert {a.value for a in NextAction} == set(ACTION_TO_NODE)
    assert set(ACTION_TO_NODE.values()) == set(EXECUTORS)
