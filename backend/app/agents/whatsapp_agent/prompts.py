"""System prompt + tool schemas for the WhatsApp conversational agent.

Kept in its own module so the prompt can be reviewed and diffed without
scrolling past plumbing, and so tests can assert on its invariants (that it
names the four qualification fields, that it forbids re-asking known
information, that it constrains message length).
"""

from typing import Any

SYSTEM_PROMPT = """\
You are the WhatsApp assistant for a real-estate sales team. You are talking to \
a prospective buyer on WhatsApp, in the same language they wrote to you in.

YOUR JOB
Move the conversation toward a qualified lead. A lead is qualified once the CRM \
knows all four of these:
  - name              the person's name
  - budget            their budget range
  - interest          what they are looking for (configuration, project, locality)
  - call_availability when a salesperson may call them

NEVER RE-ASK WHAT IS ALREADY KNOWN. The CRM snapshot below lists what is already \
on file and what is still missing. Ask only about what is listed as missing, and \
ask for at most ONE missing thing per message -- this is a chat, not a form.

FACTS
Never invent a project, unit, price, availability or amenity. If the buyer asks \
anything about inventory or pricing, call a tool and answer only from what it \
returns. If the tools give you nothing useful, say you will have a colleague \
confirm rather than guessing.

STYLE
WhatsApp length: one to three short sentences, under about 60 words. No markdown \
headings, no bullet lists, no emoji spam, no signature block. Warm and direct. \
Answer what they actually asked before steering to your next question.

You may only read. You cannot book, cancel, call, or change anything -- if the \
buyer asks for one of those, acknowledge it and say it is being arranged; the \
system performs the action separately.
"""


def build_user_prompt(
    *,
    latest_message: str,
    known: dict[str, Any],
    missing: list[str],
    lead_snapshot: dict[str, Any],
    action_hint: str,
) -> str:
    """Render the per-turn user message: the CRM snapshot, then the buyer's
    text. Volatile content last keeps the stable system prompt cacheable."""
    known_lines = (
        "\n".join(f"  - {k}: {v}" for k, v in sorted(known.items()) if v) or "  - (nothing yet)"
    )
    missing_line = ", ".join(missing) if missing else "(nothing -- the lead is fully qualified)"

    requirement = lead_snapshot.get("requirement") or {}
    interests = lead_snapshot.get("property_interests") or []
    interest_line = (
        ", ".join(
            str(i.get("project_name") or i.get("project_id")) for i in interests[:3]
        )
        or "(none recorded)"
    )

    return (
        "CRM SNAPSHOT\n"
        f"Already known (do NOT ask again):\n{known_lines}\n"
        f"Still missing: {missing_line}\n"
        f"Recorded requirements: {requirement or '(none)'}\n"
        f"Projects already of interest: {interest_line}\n"
        f"What this reply should accomplish: {action_hint}\n\n"
        "BUYER'S LATEST WHATSAPP MESSAGE\n"
        f"{latest_message}"
    )


# Read-only tool surface exposed to the composer. These names map 1:1 onto
# entries in app/agent/tools/__init__.py's READ_TOOLS set; the executor
# refuses anything not on this list, so the model cannot reach a write tool
# even if it hallucinates one.
READ_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_properties",
        "description": (
            "Search this tenant's available property units. Call this whenever the "
            "buyer asks what is available, what fits a budget, or what options exist "
            "in a configuration or locality. Returns unit code, bedrooms, facing, "
            "price, area and status for up to 5 matching units."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "budget_min": {"type": "number", "description": "Lower bound of budget."},
                "budget_max": {"type": "number", "description": "Upper bound of budget."},
                "bedrooms": {"type": "integer", "description": "Required bedroom count."},
                "limit": {"type": "integer", "description": "Max units to return (1-5)."},
            },
            "required": [],
        },
    },
    {
        "name": "get_property_details",
        "description": (
            "Authoritative detail for one property unit, by id. Call this when the "
            "buyer asks a specific question about a unit that a search already "
            "surfaced (price breakup, area, floor, facing)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string", "description": "Property UUID."},
            },
            "required": ["property_id"],
        },
    },
    {
        "name": "get_project_details",
        "description": (
            "Project-level detail by id: developer, location, amenities, status. Call "
            "this for questions about the development rather than a single unit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "get_property_availability",
        "description": (
            "Live availability of one unit by id. Call this before telling the buyer "
            "a specific unit is still available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string", "description": "Property UUID."},
            },
            "required": ["property_id"],
        },
    },
]

ALLOWED_TOOL_NAMES = frozenset(schema["name"] for schema in READ_TOOL_SCHEMAS)
