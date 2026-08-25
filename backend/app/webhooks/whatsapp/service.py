"""Inbound WhatsApp webhook event handlers -- persist inbound messages,
delivery-status updates, and template status updates, then hand the message
to the AI lead-workflow orchestrator.

AUTO-REPLY (added in the AI orchestration phase)
------------------------------------------------
`handle_inbound_message` now invokes `app.agents.orchestrator`. The ordering
is load-bearing: the message is deduplicated, resolved, STORED and instrumented
BEFORE the orchestrator runs. So the durable record of what the customer said
never depends on the AI layer working.

The orchestrator call is wrapped so that nothing it does can break this
handler (spec section 20). Concretely:

  * it is skipped entirely when the AI layer is switched off or has no API
    key -- a deployment without credentials keeps the deterministic pipeline
    working instead of logging a stack trace per message;
  * `run_lead_workflow` is documented not to raise, and the `except` here is
    the belt to that suspenders;
  * a failed turn is logged and swallowed, so the router still returns the
    200 OK Meta requires. Meta retries non-200 deliveries, and a retry would
    be deduplicated by `_record_event` -- meaning a raised AI error would
    silently cost us the reply AND the retry.

FALLBACK CHOICE: when the orchestrator itself fails (model outage, malformed
response), its own decision table routes to TRANSFER_HUMAN, so a person picks
the conversation up. When the *invocation* fails hard enough to reach the
`except` below, we log and do nothing further: the message is already stored
and visible in the dashboard, and inventing a handoff row from a handler that
just failed would be guessing.

Inbound messages are deduplicated through public.webhook_events keyed on
(provider, external_event_id) = ('meta_whatsapp', wamid), so a retried Meta
delivery cannot insert a second whatsapp_messages row.

Contact, lead and conversation resolution are NOT implemented here: they go
through the shared deterministic foundation layer
(app/customers/resolver.py, app/leads/resolver.py,
app/conversations/repository.py) so every channel resolves identically.
"""

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import is_llm_configured
from app.agents.orchestrator import new_state, run_lead_workflow
from app.conversations.model import ConversationChannel
from app.conversations.repository import get_or_create_conversation
from app.core.config import settings
from app.customers.resolver import ContactResolver
from app.events.model import EventType
from app.events.publisher import publish_event
from app.leads.resolver import LeadResolver
from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
)
from app.whatsapp.repository import WhatsAppRepository

logger = logging.getLogger(__name__)

_META_WHATSAPP_PROVIDER = "meta_whatsapp"


class WhatsAppWebhookService:
    """Processes parsed WhatsApp webhook events for one tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = WhatsAppRepository(session)
        self.contact_resolver = ContactResolver(session)
        self.lead_resolver = LeadResolver(session)

    # ------------------------------------------------------------------
    # Idempotency / audit log
    # ------------------------------------------------------------------

    async def _record_event(
        self,
        *,
        tenant_id: UUID | None,
        provider: str,
        event_type: str,
        external_event_id: str,
        payload: dict[str, Any],
    ) -> bool:
        """Persist the raw delivery. Returns True if this is a NEW event
        (should be processed), False if it's a duplicate delivery (no-op).

        The WHERE clause mirrors the predicate of the partial unique index
        uq_webhook_events_provider_external_notnull (see migration 016) --
        without it Postgres cannot infer a partial index as the ON CONFLICT
        arbiter and raises 42P10.
        """
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.webhook_events (
                    tenant_id, provider, event_type, external_event_id,
                    payload, processing_status
                ) VALUES (
                    :tenant_id, :provider, :event_type, :external_event_id,
                    :payload, 'received'::public.webhook_status
                )
                ON CONFLICT (provider, external_event_id)
                    WHERE external_event_id IS NOT NULL
                    DO NOTHING
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "event_type": event_type,
                "external_event_id": external_event_id,
                # asyncpg's jsonb codec expects an already-serialized string,
                # not a dict -- see app.webhooks.superfone.service for the
                # same fix, found live-testing against a real database.
                "payload": json.dumps(payload, default=str),
            },
        )
        inserted = result.mappings().one_or_none()
        if inserted is None:
            logger.info(
                f"Duplicate webhook delivery ignored: provider={provider} "
                f"event_type={event_type} external_event_id={external_event_id}"
            )
            return False
        return True

    async def _mark_event_processed(
        self, provider: str, external_event_id: str, *, error: str | None = None
    ) -> None:
        await self.session.execute(
            text(
                """
                UPDATE public.webhook_events
                SET processing_status = CAST(:status AS public.webhook_status),
                    processed_at = NOW(),
                    error_message = :error
                WHERE provider = :provider AND external_event_id = :external_event_id
                """
            ),
            {
                "status": "failed" if error else "processed",
                "error": error,
                "provider": provider,
                "external_event_id": external_event_id,
            },
        )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def handle_inbound_message(self, tenant_id: UUID, event: InboundMessageEvent) -> None:
        """Resolve contact -> lead -> conversation, persist the message and a
        communication_logs entry, then record a MESSAGE_RECEIVED event.
        No AI reply is generated.

        A repeat delivery of the same wamid is a no-op.

        NOTE (behaviour change, deliberate): resolution now goes through
        LeadResolver, which OPENS a lead when the contact has no open one --
        the previous `get_lead_for_customer` lookup left `lead_id` NULL for a
        first-time sender. An inbound WhatsApp message is a lead, and
        downstream call/handoff machinery requires a non-null lead_id.
        """
        is_new = await self._record_event(
            tenant_id=tenant_id,
            provider=_META_WHATSAPP_PROVIDER,
            event_type="inbound_message",
            external_event_id=event.wa_message_id,
            payload=event.model_dump(),
        )
        if not is_new:
            return

        phone = f"+{event.from_phone}"
        contact = await self.contact_resolver.resolve_contact(
            tenant_id,
            phone=phone,
            defaults={
                "full_name": event.contact_name or phone,
                "source": "whatsapp",
                # Someone who messages the business on WhatsApp has, by Meta's
                # own rules, opted into being replied to on WhatsApp.
                "whatsapp_opted_in": True,
            },
        )
        contact_id = contact["id"]

        lead = await self.lead_resolver.resolve_lead(
            tenant_id, contact_id, context={"source": "whatsapp"}
        )
        lead_id = lead["id"]

        conversation = await get_or_create_conversation(
            self.session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            channel=ConversationChannel.WHATSAPP,
            external_thread_id=event.from_phone,
            lead_id=lead_id,
        )
        conversation_id = conversation["id"]

        message_row = await self.repository.create_message(
            tenant_id=tenant_id,
            customer_id=contact_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            direction="inbound",
            provider_message_id=event.wa_message_id,
            wa_id=event.from_phone,
            phone_to=None,
            phone_from=event.from_phone,
            message_type=event.message_type,
            content={"body": event.text} if event.text else {},
            template_variables={},
            status="delivered",
            sent_at=None,
        )

        await self.repository.add_communication_log(
            tenant_id=tenant_id,
            customer_id=contact_id,
            lead_id=lead_id,
            whatsapp_message_id=message_row["id"],
            direction="inbound",
            status="delivered",
            summary="WhatsApp message received",
            initiated_by="system",
            initiated_by_id=None,
        )

        await publish_event(
            self.session,
            tenant_id=tenant_id,
            event_type=EventType.MESSAGE_RECEIVED,
            contact_id=contact_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            payload={
                "channel": str(ConversationChannel.WHATSAPP),
                "provider_message_id": event.wa_message_id,
                "message_type": event.message_type,
                "whatsapp_message_id": str(message_row["id"]),
            },
        )

        await self._mark_event_processed(_META_WHATSAPP_PROVIDER, event.wa_message_id)

        await self._run_orchestrator(
            tenant_id=tenant_id,
            contact_id=contact_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            text=event.text,
        )

    async def _run_orchestrator(
        self,
        *,
        tenant_id: UUID,
        contact_id: UUID,
        lead_id: UUID | None,
        conversation_id: UUID | None,
        text: str | None,
    ) -> None:
        """Hand the stored message to the AI lead workflow. Never raises --
        see the module docstring for why that matters to Meta's retries."""
        if not settings.AI_ORCHESTRATOR_ENABLED or not is_llm_configured():
            logger.debug(
                "AI orchestrator skipped for inbound WhatsApp message "
                "(disabled or no ANTHROPIC_API_KEY configured)."
            )
            return
        if not (text or "").strip():
            # Media-only messages carry nothing to classify. The message row
            # is already stored; a human can see it in the dashboard.
            return

        try:
            final = await run_lead_workflow(
                self.session,
                new_state(
                    tenant_id=tenant_id,
                    contact_id=contact_id,
                    lead_id=lead_id,
                    conversation_id=conversation_id,
                    latest_message=text,
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- webhook must still return 200
            logger.exception(f"AI orchestrator invocation failed: {exc!s}")
            return

        if final.get("error"):
            logger.warning(
                f"AI orchestrator finished with an error for lead={lead_id}: "
                f"{final.get('error')} (action={final.get('next_action')})"
            )
        else:
            logger.info(
                f"AI orchestrator handled inbound WhatsApp message for lead={lead_id}: "
                f"action={final.get('next_action')}"
            )

    async def handle_status_update(self, event: StatusUpdateEvent) -> None:
        """Update the matching message's delivery status. A status event
        for a wamid this tenant never sent through this integration is
        skipped, not an error (e.g. a message sent before this pipeline
        existed)."""
        updated = await self.repository.update_message_status_by_provider_id(
            event.wa_message_id,
            event.status,
            delivered_at=None,
            read_at=None,
            failed_at=None,
            failure_code=None,
            failure_reason=event.error_message,
        )
        if not updated:
            logger.info(
                f"whatsapp webhook: status update for unknown wamid "
                f"{event.wa_message_id!r}, skipping"
            )
            return

        await self.repository.add_communication_log(
            tenant_id=updated["tenant_id"],
            customer_id=updated["customer_id"],
            lead_id=updated["lead_id"],
            whatsapp_message_id=updated["id"],
            direction="outbound",
            status=event.status,
            summary=f"WhatsApp message {event.status}",
            initiated_by="system",
            initiated_by_id=None,
        )

    async def handle_template_status_update(self, event: TemplateStatusUpdateEvent) -> None:
        """Update the matching template's approval status."""
        await self.repository.update_template_status_by_provider_id(
            event.provider_template_id, event.status, event.rejection_reason
        )
