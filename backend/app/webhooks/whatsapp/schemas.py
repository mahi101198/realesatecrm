"""Typed events parsed from Meta's WhatsApp webhook payload. Meta's own
envelope (entry[].changes[].{field,value}) is not modeled 1:1 as Pydantic
input -- parse_webhook_events walks the raw dict defensively (payload
shape is attacker-reachable, so any single malformed entry is skipped
rather than raising and losing every other event in the same delivery)."""

import logging
from typing import Any, Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_STATUS_MAP: dict[str, Literal["sent", "delivered", "read", "failed"]] = {
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "failed": "failed",
}

_TEMPLATE_STATUS_MAP: dict[str, Literal["approved", "rejected", "paused"]] = {
    "APPROVED": "approved",
    "REJECTED": "rejected",
    "PAUSED": "paused",
    "DISABLED": "paused",
}


class InboundMessageEvent(BaseModel):
    """A customer-sent WhatsApp message."""

    from_phone: str
    contact_name: str | None
    wa_message_id: str
    text: str | None
    message_type: str
    timestamp: str


class StatusUpdateEvent(BaseModel):
    """A delivery-status callback for a message this tenant sent."""

    wa_message_id: str
    status: Literal["sent", "delivered", "read", "failed"]
    error_message: str | None = None


class TemplateStatusUpdateEvent(BaseModel):
    """A template approval/rejection/pause status change."""

    provider_template_id: str
    status: Literal["approved", "rejected", "paused"]
    rejection_reason: str | None = None


WebhookEvent = InboundMessageEvent | StatusUpdateEvent | TemplateStatusUpdateEvent


def parse_webhook_events(payload: dict[str, Any]) -> list[WebhookEvent]:
    """Walk Meta's entry[].changes[] envelope and return typed events.
    Any single malformed change is logged and skipped rather than raising
    and losing every other event in the same webhook delivery."""
    events: list[WebhookEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value", {})
            try:
                if field == "messages":
                    events.extend(_parse_messages_change(value))
                elif field == "message_template_status_update":
                    events.append(_parse_template_status_change(value))
            except (KeyError, IndexError, TypeError) as e:
                logger.warning(
                    f"whatsapp webhook: skipping malformed change (field={field!r}): {e!s}"
                )
    return events


def _parse_messages_change(value: dict[str, Any]) -> list[WebhookEvent]:
    events: list[WebhookEvent] = []
    contacts_by_wa_id = {c["wa_id"]: c for c in value.get("contacts", [])}

    for message in value.get("messages", []):
        wa_id = message["from"]
        contact = contacts_by_wa_id.get(wa_id)
        contact_name = contact["profile"]["name"] if contact and contact.get("profile") else None
        message_type = message.get("type", "text")
        text = message.get(message_type, {}).get("body") if message_type == "text" else None
        events.append(
            InboundMessageEvent(
                from_phone=wa_id,
                contact_name=contact_name,
                wa_message_id=message["id"],
                text=text,
                message_type=message_type,
                timestamp=message.get("timestamp", ""),
            )
        )

    for status_entry in value.get("statuses", []):
        raw_status = status_entry["status"]
        mapped_status = _STATUS_MAP.get(raw_status)
        if mapped_status is None:
            logger.warning(f"whatsapp webhook: unrecognized status {raw_status!r}, skipping")
            continue
        errors = status_entry.get("errors") or []
        error_message = errors[0].get("message") if errors else None
        events.append(
            StatusUpdateEvent(
                wa_message_id=status_entry["id"], status=mapped_status, error_message=error_message
            )
        )

    return events


def _parse_template_status_change(value: dict[str, Any]) -> TemplateStatusUpdateEvent:
    raw_status = value["event"]
    mapped_status = _TEMPLATE_STATUS_MAP.get(raw_status, "paused")
    return TemplateStatusUpdateEvent(
        provider_template_id=str(value["message_template_id"]),
        status=mapped_status,
        rejection_reason=value.get("reason"),
    )
