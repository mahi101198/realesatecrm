"""System prompt, tool surface and outcome schema for the Voice Agent.

Separated from `agent.py` for the same reason the WhatsApp agent separates
them: the prompt is the part a non-engineer reviews, and tests assert on its
invariants without importing the LLM plumbing.

THE TOOL SURFACE IS SHARED WITH THE WHATSAPP AGENT ON PURPOSE
    `READ_TOOL_SCHEMAS` is re-exported from `app.agents.whatsapp_agent.prompts`
    rather than re-declared. This is an import of a DATA CONSTANT, not an
    agent-to-agent call (spec section 17 forbids the latter, not the former):
    both agents are entitled to exactly the same four read-only lookups against
    the same registry, and duplicating the list would let the two allowlists
    drift until one agent quietly gained a capability the other's security
    review never saw.
"""

from typing import Any

from app.agents.whatsapp_agent.prompts import (
    ALLOWED_TOOL_NAMES as _WHATSAPP_ALLOWED_TOOL_NAMES,
)
from app.agents.whatsapp_agent.prompts import (
    READ_TOOL_SCHEMAS as _WHATSAPP_READ_TOOL_SCHEMAS,
)
from app.voice.context import VoiceCallContext

READ_TOOL_SCHEMAS: list[dict[str, Any]] = _WHATSAPP_READ_TOOL_SCHEMAS
ALLOWED_TOOL_NAMES = _WHATSAPP_ALLOWED_TOOL_NAMES

# A spoken turn is not a chat turn. Anything longer than this stops being a
# conversation and starts being an announcement the customer will talk over.
MAX_SPOKEN_REPLY_CHARS = 320

SYSTEM_PROMPT = """\
You are a real-estate sales assistant speaking with a customer ON A PHONE CALL.

LANGUAGE
Default to Hindi/Hinglish -- natural code-mixed Hindi-English, the way Indian \
real-estate sales calls actually sound (not pure textbook Hindi, not pure \
English). Use this for your OPENING LINE too, before the customer has said \
anything -- do not default to English just because there is nothing to react \
to yet. If the customer replies in pure English throughout, switch fully to \
English to match them. The briefing's "Preferred language" line is the \
strongest signal you have; follow it.

THIS IS SPEECH, NOT TEXT
Your reply is read aloud. One or two sentences, under about 40 words. No lists, \
no markdown, no URLs, no emoji, no spelling things out. Numbers as a person would \
say them. If you must convey several options, offer two and ask which they prefer.

YOUR JOB ON THIS CALL
  - Continue qualification: fill in what the CRM is still missing.
  - Discuss what they are looking for and answer questions about the inventory.
  - Detect what they actually want: more information, a site visit, a callback \
later, a human salesperson, or to be left alone.
  - If this is a callback they asked for, open by acknowledging that.

NEVER RE-ASK WHAT IS ALREADY KNOWN. The briefing below is what previous \
conversations (including WhatsApp) already established. Treat it as true and \
build on it. Re-asking it tells the customer nobody is listening.

FACTS
Never invent a project, unit, price, availability or amenity. Call a tool and \
answer only from what it returns. If the tools give you nothing useful, say a \
colleague will confirm -- do not guess on a call, it cannot be edited later.

YOU MAY ONLY READ
You cannot book, cancel, reschedule or change anything. If they ask for one of \
those, say it is being arranged; the system performs it separately after the call.

WHEN TO ASK FOR A HUMAN
If they are angry, negotiating price or terms, asking something legal or \
financial you cannot source from a tool, or they simply ask for a person -- say \
you are putting them through to a colleague and stop selling.

ENDING
If they ask to end the call, or say they are not interested, acknowledge it \
warmly and briefly and stop. Do not push.
"""


# Bare ISO codes ("hi", "en") are data, not instructions -- a model handed
# "Preferred language: hi" in a list of facts has no obligation to read that as
# "speak Hindi/Hinglish for this whole call." Spelling out what each code means
# AS AN INSTRUCTION is what actually changes the model's behaviour; see
# SYSTEM_PROMPT's LANGUAGE section, which this reinforces per-call. "hi" maps
# to Hinglish rather than pure Hindi because that is what this product's
# customers are actually spoken to in -- see SYSTEM_PROMPT.
_LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "hi": "Hindi/Hinglish (natural code-mixed Hindi-English)",
    "en": "English",
}


def _language_instruction(code: str) -> str:
    return _LANGUAGE_INSTRUCTIONS.get(code, f"Hindi/Hinglish (no specific mapping for '{code}')")


def build_briefing(context: VoiceCallContext) -> str:
    """Render the spec-section-4 context bundle as the agent's briefing block.

    Absent fields are rendered as an explicit "not known" rather than omitted,
    so the model can tell "we have no budget on file" (ask for it) from "the
    briefing was truncated" (do not assume).
    """
    lines = [
        "CALL BRIEFING (already established -- do NOT ask for any of this again)",
        f"  Customer name: {context.customer_name or '(not known)'}",
        f"  Preferred language: speak in {_language_instruction(context.preferred_language)}.",
        f"  What they are looking for: {context.interest or '(not known)'}",
        f"  Budget: {context.budget or '(not known)'}",
        f"  Why we are calling: {context.reason_for_call or '(routine follow-up)'}",
    ]
    summary = context.conversation_summary
    lines.append(
        "  Summary of previous conversations: "
        + (summary if summary else "(no previous conversation on file)")
    )
    relationship = (context.lead_snapshot or {}).get("relationship") or {}
    if relationship.get("previous_calls"):
        lines.append(
            f"  Previous calls: {relationship['previous_calls']}"
            f" (last outcome: {relationship.get('last_outcome') or 'unknown'})"
        )
    sales_context = (context.lead_snapshot or {}).get("sales_context") or {}
    objections = sales_context.get("main_objections") or []
    if objections:
        lines.append(f"  Objections raised before: {'; '.join(str(o) for o in objections[:3])}")
    return "\n".join(lines)


def build_turn_prompt(
    context: VoiceCallContext, transcript: list[dict[str, str]], utterance: str
) -> str:
    """Render one turn: briefing, then transcript so far, then what they just said.

    Volatile content last keeps the stable system prompt cacheable, same as the
    WhatsApp agent.
    """
    history = (
        "\n".join(f"  {turn['speaker']}: {turn['text']}" for turn in transcript[-12:])
        or "  (this is the opening of the call)"
    )
    return (
        f"{build_briefing(context)}\n\n"
        f"CALL SO FAR\n{history}\n\n"
        f"CUSTOMER JUST SAID\n{utterance}"
    )


def build_opening_prompt(context: VoiceCallContext) -> str:
    """Render the prompt for the agent's very first spoken line.

    A separate prompt because the first turn has no customer utterance to react
    to, and an opener that greets by name and states why we are calling is the
    single biggest driver of whether the call continues at all.
    """
    return (
        f"{build_briefing(context)}\n\n"
        "TASK\nThe customer has just picked up. Greet them by name if you have "
        "one, say who is calling and why in one sentence, and ask one opening "
        "question. Nothing else."
    )


# The structured post-call result. `outcome` values are EXACTLY the members of
# the public.call_attempt_outcome enum that a connected AI call can legitimately
# produce -- 'no_answer'/'busy'/'rejected'/'wrong_number' are telephony facts
# Superfone reports, not conclusions this agent may draw, and 'technical_failure'
# is set by the failure path in `agent.py`, never by the model.
OUTCOME_VALUES: tuple[str, ...] = (
    "connected",
    "customer_requested_callback",
    "customer_not_interested",
    "site_visit_scheduled",
    "human_transfer",
)

OUTCOME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "outcome": {
            "type": "string",
            "enum": list(OUTCOME_VALUES),
            "description": "How this call ended, from the customer's perspective.",
        },
        "summary": {
            "type": "string",
            "description": "Two or three sentences: what was discussed and agreed.",
        },
        "customer_intent": {
            "type": "string",
            "description": "The customer's dominant intent, a short phrase.",
        },
        "human_transfer_required": {
            "type": "boolean",
            "description": "True if a human salesperson must call this customer.",
        },
        "callback_requested": {
            "type": "boolean",
            "description": "True if the customer asked to be called again later.",
        },
        "objections": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concerns the customer raised, verbatim-ish and short.",
        },
        "next_best_action": {
            "type": "string",
            "description": "The single next step a human should take.",
        },
    },
    "required": [
        "outcome",
        "summary",
        "customer_intent",
        "human_transfer_required",
        "callback_requested",
        "objections",
        "next_best_action",
    ],
}

OUTCOME_SYSTEM_PROMPT = """\
You are summarising a finished sales phone call for a CRM. Report only what the \
transcript supports. Do not infer enthusiasm the customer did not express, and do \
not record a commitment they did not make. If the call ended without a clear \
result, that is 'connected' -- not a guess at what they might have meant.
"""
