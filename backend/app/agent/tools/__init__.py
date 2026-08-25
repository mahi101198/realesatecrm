"""AI Agent Tools Registry, Classification & Dispatcher Package."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.repository import AgentRepository
from app.agent.tools.call_tools import (
    make_instant_call,
    make_instant_call_tool,
    schedule_call,
    schedule_call_tool,
)
from app.agent.tools.read_tools import (
    check_site_visit_availability_tool,
    get_lead_context_tool,
    get_open_follow_ups_tool,
    get_project_details_tool,
    get_property_availability_tool,
    get_property_details_tool,
    get_recent_call_summary_tool,
    search_properties_tool,
)
from app.agent.tools.write_tools import (
    add_lead_note_tool,
    cancel_follow_up_tool,
    cancel_site_visit_tool,
    create_follow_up_tool,
    create_sales_callback_tool,
    record_call_observation_tool,
    record_lead_property_interest_tool,
    request_sales_agent_transfer_tool,
    reschedule_follow_up_tool,
    reschedule_site_visit_tool,
    schedule_site_visit_tool,
    update_customer_details_tool,
    update_lead_requirements_tool,
    update_lead_score_tool,
)
from app.core.request_context import RequestContext

logger = logging.getLogger(__name__)

AgentTool = Callable[..., Awaitable[dict[str, Any]]]

# Map tool names to handler functions
TOOL_REGISTRY: dict[str, AgentTool] = {
    "get_lead_context": get_lead_context_tool,
    "get_recent_call_summary": get_recent_call_summary_tool,
    "get_open_follow_ups": get_open_follow_ups_tool,
    "search_properties": search_properties_tool,
    "get_property_details": get_property_details_tool,
    "get_project_details": get_project_details_tool,
    "get_property_availability": get_property_availability_tool,
    "update_lead_requirements": update_lead_requirements_tool,
    "update_customer_details": update_customer_details_tool,
    "add_lead_note": add_lead_note_tool,
    "record_call_observation": record_call_observation_tool,
    "create_follow_up": create_follow_up_tool,
    "reschedule_follow_up": reschedule_follow_up_tool,
    "cancel_follow_up": cancel_follow_up_tool,
    "check_site_visit_availability": check_site_visit_availability_tool,
    "schedule_site_visit": schedule_site_visit_tool,
    "reschedule_site_visit": reschedule_site_visit_tool,
    "cancel_site_visit": cancel_site_visit_tool,
    "request_sales_agent_transfer": request_sales_agent_transfer_tool,
    "create_sales_callback": create_sales_callback_tool,
    "record_lead_property_interest": record_lead_property_interest_tool,
    "update_lead_score": update_lead_score_tool,
    # Call actions (foundation layer): make_instant_call is the single code
    # path that places a call; schedule_call only persists a call_jobs row.
    "make_instant_call": make_instant_call_tool,
    "schedule_call": schedule_call_tool,
}

# Tool Classification: READ vs WRITE
READ_TOOLS = {
    "get_lead_context",
    "get_recent_call_summary",
    "get_open_follow_ups",
    "search_properties",
    "get_property_details",
    "get_project_details",
    "get_property_availability",
    "check_site_visit_availability",
}

WRITE_TOOLS = set(TOOL_REGISTRY.keys()) - READ_TOOLS


async def dispatch_agent_tool(
    tool_name: str,
    context: RequestContext,
    session: AsyncSession,
    arguments: dict[str, Any],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Dynamically dispatch tool call to registered tool handler with
    idempotency and audit logging."""
    handler = TOOL_REGISTRY.get(tool_name)
    if not handler:
        return {
            "success": False,
            "error_code": "UNKNOWN_TOOL",
            "message": f"Tool '{tool_name}' is not registered in the system.",
        }

    tenant_id = context.tenant_id
    repo = AgentRepository(session)

    # 1. Idempotency Check for WRITE tools
    if idempotency_key and tool_name in WRITE_TOOLS and tenant_id:
        cached = await repo.get_idempotent_response(tenant_id, idempotency_key, tool_name)
        if cached is not None:
            logger.info(
                f"Returning cached idempotent response for tool '{tool_name}' "
                f"key '{idempotency_key}'"
            )
            return cached

    # 2. Execute Handler
    try:
        result = await handler(context, session, **arguments)

        # 3. Save Idempotency Result if successful
        if idempotency_key and tool_name in WRITE_TOOLS and tenant_id and result.get("success"):
            await repo.save_idempotent_response(tenant_id, idempotency_key, tool_name, result)

        # 4. Audit Log for WRITE tools
        #
        # BUGFIX: this INSERT previously named columns public.activities does
        # not have (user_id / entity_type / entity_id -- see migration 009), so
        # it raised every single time and was silently swallowed by the except
        # below. No AI tool execution was ever actually audited. The column
        # list now matches the real table, which is also what feeds the
        # "prior AI actions" section of a sales handoff's context bundle.
        if tool_name in WRITE_TOOLS and tenant_id:
            try:
                audit_sql = text(
                    """
                    INSERT INTO public.activities (
                        tenant_id, lead_id, customer_id, activity_type,
                        actor_type, actor_id, title, description, metadata
                    ) VALUES (
                        :tenant_id, :lead_id, :customer_id, 'ai_tool_execution',
                        'ai_agent', :actor_id, :title, :description, :metadata
                    )
                    """
                )
                await session.execute(
                    audit_sql,
                    {
                        "tenant_id": tenant_id,
                        "lead_id": arguments.get("lead_id"),
                        "customer_id": arguments.get("customer_id"),
                        "actor_id": context.user_id,
                        "title": tool_name,
                        "description": f"AI Agent executed write tool {tool_name}",
                        "metadata": {"source": "ai_agent", "tool": tool_name},
                    },
                )
            except Exception as audit_err:
                logger.warning(
                    f"Failed to record audit activity for tool '{tool_name}': {audit_err!s}"
                )

        return result
    except TypeError as e:
        logger.error(f"Invalid arguments for tool '{tool_name}': {e!s}")
        return {
            "success": False,
            "error_code": "INVALID_TOOL_ARGUMENTS",
            "message": f"Invalid arguments supplied for tool '{tool_name}': {e!s}",
        }
    except Exception as e:
        logger.error(f"Unhandled exception executing tool '{tool_name}': {e!s}")
        return {
            "success": False,
            "error_code": "TOOL_EXECUTION_ERROR",
            "message": f"Execution error in tool '{tool_name}': {e!s}",
        }


__all__ = [
    "TOOL_REGISTRY",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "dispatch_agent_tool",
    "get_lead_context_tool",
    "get_recent_call_summary_tool",
    "get_open_follow_ups_tool",
    "search_properties_tool",
    "get_property_details_tool",
    "get_project_details_tool",
    "get_property_availability_tool",
    "update_lead_requirements_tool",
    "update_customer_details_tool",
    "add_lead_note_tool",
    "record_call_observation_tool",
    "create_follow_up_tool",
    "reschedule_follow_up_tool",
    "cancel_follow_up_tool",
    "check_site_visit_availability_tool",
    "schedule_site_visit_tool",
    "reschedule_site_visit_tool",
    "cancel_site_visit_tool",
    "request_sales_agent_transfer_tool",
    "create_sales_callback_tool",
    "record_lead_property_interest_tool",
    "update_lead_score_tool",
    "make_instant_call",
    "make_instant_call_tool",
    "schedule_call",
    "schedule_call_tool",
]
