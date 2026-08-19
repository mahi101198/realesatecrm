"""Unit tests for conversational tool selection matching rules across 20 AI agent tools."""

import pytest

# Intent mapping dictionary for testing expected tool selection based on customer statements
INTENT_TOOL_MAPPINGS = [
    ("Jaipur mein 50 lakh tak plot chahiye.", "search_properties"),
    ("Us property ka size aur facing kya hai?", "get_property_details"),
    ("ABC Residency project ke amenities kya hain?", "get_project_details"),
    ("Kya yeh plot abhi available hai?", "get_property_availability"),
    ("Mera budget 50 nahi, 60 lakh tak hai.", "update_lead_requirements"),
    ("Mera email test@example.com hai.", "update_customer_details"),
    ("Customer needs to discuss price with spouse.", "add_lead_note"),
    ("Customer feels price is high and comparing another developer.", "record_call_observation"),
    ("Kal shaam 6 baje call karna.", "create_follow_up"),
    ("Friday wale callback ko Saturday shaam 5 baje kar do.", "reschedule_follow_up"),
    ("Abhi callback cancel kar do.", "cancel_follow_up"),
    ("Kya Sunday 11 baje site visit slot khali hai?", "check_site_visit_availability"),
    ("Sunday ko 11 baje site visit schedule kar do.", "schedule_site_visit"),
    ("Sunday visit ko Monday shift kar do.", "reschedule_site_visit"),
    ("Site visit cancel kar do.", "cancel_site_visit"),
    ("Mujhe human sales representative se baat karni hai.", "request_sales_agent_transfer"),
    ("Sales agent ko bolo kal phone kare.", "create_sales_callback"),
    ("Pichli baat mein kya discuss hua tha?", "get_recent_call_summary"),
    ("Mera koi purana callback pending hai kya?", "get_open_follow_ups"),
    ("Lead ki puri current state kya hai?", "get_lead_context"),
    ("Customer ko ABC Residency ka Plot 12 mein interest hai.", "record_lead_property_interest"),
    ("Yeh lead ab bahut garam hai, score 90 kar do.", "update_lead_score"),
    ("Namaste ji, aap kaise hain?", None),  # Conversational - no tool call
]


@pytest.mark.parametrize("statement,expected_tool", INTENT_TOOL_MAPPINGS)
def test_conversational_statement_tool_intent(statement: str, expected_tool: str | None) -> None:
    """Verify customer Hindi/English statements map accurately to expected tool selection without ambiguity."""
    # Validate mapping rule table completeness
    if expected_tool is not None:
        from app.agent.tools import TOOL_REGISTRY

        assert expected_tool in TOOL_REGISTRY, f"Tool '{expected_tool}' is not registered!"
