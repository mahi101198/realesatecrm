"""The WhatsApp conversational agent.

WHAT IT IS
    A composer. Given the buyer's latest message plus the CRM's view of the
    lead, it writes one short WhatsApp reply and sends it.

WHAT IT IS NOT
    It does not decide what should happen next -- the orchestrator does that
    and calls in here with an already-chosen `action_hint`.
    It does not call the orchestrator, and it does not call the voice agent in
    `app/voice/` -- CALL_NOW / CALL_LATER are routed by the orchestrator
    straight to `app.agent.tools.call_tools`, never through an agent object,
    and the voice agent picks the call up from the telephony side. Agent-to-
    agent calls are forbidden by spec section 17; `public.events` is the seam.
    It does not write to the database. Its entire tool surface is the four
    read-only property/project lookups in `prompts.READ_TOOL_SCHEMAS`, and
    `_execute_tool` refuses any name outside that allowlist -- so a
    hallucinated `schedule_site_visit` call is rejected before it reaches the
    registry (spec sections 3 and 17).

OUTBOUND PATH
    Sending goes through the existing `WhatsAppService.send_message`, which
    owns the per-tenant Meta client, the 24-hour session-window rule, the
    `whatsapp_messages` row, the communication log and the MESSAGE_SENT event.
    There is deliberately no second outbound path in this package.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import llm
from app.agents.security import build_agent_read_context
from app.agents.tool_args import coerce_uuid_arguments
from app.agents.whatsapp_agent.prompts import (
    ALLOWED_TOOL_NAMES,
    READ_TOOL_SCHEMAS,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.core.request_context import RequestContext
from app.whatsapp.schemas import WhatsAppSendRequest
from app.whatsapp.service import WhatsAppService

logger = logging.getLogger(__name__)

# WhatsApp is a chat surface; a wall of text reads as spam and risks the
# recipient blocking the business number. Hard-trim rather than trusting the
# prompt alone.
MAX_REPLY_CHARS = 900


class WhatsAppAgent:
    """Composes and sends one outbound WhatsApp reply for one tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.whatsapp = WhatsAppService(session)

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    async def compose(
        self,
        *,
        tenant_id: UUID,
        latest_message: str,
        known: dict[str, Any],
        missing: list[str],
        lead_snapshot: dict[str, Any],
        action_hint: str,
    ) -> str:
        """Return the reply text. Raises `llm.LLMError` if the model layer
        cannot produce one -- the orchestrator decides what to do about that
        (it falls back to a human handoff)."""
        read_context = build_agent_read_context(tenant_id)

        async def tool_executor(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return await self._execute_tool(read_context, name, arguments)

        text = await llm.call_with_tools(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(
                latest_message=latest_message,
                known=known,
                missing=missing,
                lead_snapshot=lead_snapshot,
                action_hint=action_hint,
            ),
            tools=READ_TOOL_SCHEMAS,
            tool_executor=tool_executor,
        )
        return text.strip()[:MAX_REPLY_CHARS]

    async def _execute_tool(
        self, read_context: RequestContext, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch one model-requested tool call.

        The allowlist check is the security boundary, not a nicety: the model
        picks the name, so the name is untrusted input. Even if it named a
        write tool, `read_context` holds no write permissions and the tool
        would refuse -- this is the belt to that suspenders.
        """
        if name not in ALLOWED_TOOL_NAMES:
            logger.warning(f"WhatsApp agent requested non-allowlisted tool {name!r}; refusing.")
            return {
                "success": False,
                "error_code": "TOOL_NOT_ALLOWED",
                "message": f"Tool '{name}' is not available to this agent.",
            }

        # Imported lazily so this module does not pull the whole tool registry
        # (and its service graph) at import time.
        from app.agent.tools import TOOL_REGISTRY

        handler = TOOL_REGISTRY.get(name)
        if handler is None:  # pragma: no cover -- allowlist is a registry subset
            return {"success": False, "error_code": "UNKNOWN_TOOL", "message": name}

        coerced = coerce_uuid_arguments(arguments)
        return await handler(read_context, self.session, **coerced)

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    async def send(
        self,
        *,
        tenant_id: UUID,
        contact_id: UUID,
        lead_id: UUID | None,
        text: str,
        context_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send an already-composed reply through the existing outbound path."""
        response = await self.whatsapp.send_message(
            tenant_id,
            None,  # created_by: system-initiated, no human sender
            WhatsAppSendRequest(
                customer_id=contact_id,
                lead_id=lead_id,
                message_type="text",
                message={"body": text},
                context_message_id=context_message_id,
            ),
        )
        return {
            "whatsapp_message_id": str(response.id),
            "provider_message_id": response.provider_message_id,
        }

    async def respond(
        self,
        *,
        tenant_id: UUID,
        contact_id: UUID,
        lead_id: UUID | None,
        latest_message: str,
        known: dict[str, Any],
        missing: list[str],
        lead_snapshot: dict[str, Any],
        action_hint: str,
    ) -> dict[str, Any]:
        """Compose then send. The orchestrator's single entry point."""
        text = await self.compose(
            tenant_id=tenant_id,
            latest_message=latest_message,
            known=known,
            missing=missing,
            lead_snapshot=lead_snapshot,
            action_hint=action_hint,
        )
        sent = await self.send(
            tenant_id=tenant_id, contact_id=contact_id, lead_id=lead_id, text=text
        )
        return {"reply_text": text, **sent}
