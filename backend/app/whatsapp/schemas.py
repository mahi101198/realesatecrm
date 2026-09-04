"""WhatsApp Messaging Domain Pydantic Schemas."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

WhatsAppMessageType = Literal[
    "template",
    "text",
    "image",
    "video",
    "audio",
    "document",
    "location",
    "interactive",
    "contacts",
    "reaction",
]


class WhatsAppSendRequest(BaseModel):
    """Request payload to send a WhatsApp message to a customer.

    For message_type='template': template_name, language, and components
    are required (components uses Meta's format -- header/body/button
    parameter objects, [] if the template has no variables).
    For any other message_type: `message` is required (e.g. {"body": "hi"}
    for text). Non-template sends are subject to the 24-hour session-window
    rule -- enforced in WhatsAppService.send_message, not here.
    """

    customer_id: UUID = Field(..., description="Recipient customer")
    lead_id: UUID | None = Field(default=None, description="Associated lead, if any")
    message_type: WhatsAppMessageType = "text"
    template_name: str | None = None
    language: str | None = None
    components: list[dict[str, Any]] = Field(default_factory=list)
    message: dict[str, Any] | None = Field(
        default=None, description='e.g. {"body": "hi"} for type=text'
    )
    context_message_id: str | None = Field(
        default=None, description="wamid this message replies to, if any"
    )

    @model_validator(mode="after")
    def _validate_type_specific_fields(self) -> "WhatsAppSendRequest":
        if self.message_type == "template":
            if not self.template_name or not self.language:
                raise ValueError("template_name and language are required for type=template.")
        elif not self.message:
            raise ValueError(f"message is required for type={self.message_type}.")
        return self


class WhatsAppMessageResponse(BaseModel):
    """WhatsApp message API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    customer_id: UUID
    lead_id: UUID | None = None
    template_id: UUID | None = None
    direction: str
    provider_message_id: str | None = None
    wa_id: str | None = None
    phone_to: str | None = None
    phone_from: str | None = None
    message_type: str
    content: dict[str, Any]
    template_variables: dict[str, Any]
    status: str
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    failed_at: datetime | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Joined human-readable fields
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    customer_city: str | None = None
    lead_number: str | None = None
    lead_status: str | None = None


class WhatsAppMessageFilter(BaseModel):
    """Filter parameters for listing WhatsApp messages."""

    customer_id: UUID | None = None
    lead_id: UUID | None = None
    direction: str | None = None
    status: str | None = None


class WhatsAppTemplateResponse(BaseModel):
    """A single template as returned live by Superfone's list-templates API
    (passthrough, not a local DB read -- see app/whatsapp/service.py)."""

    id: str
    name: str
    language: str
    status: str
    category: str
    parameter_format: str | None = None
    components: list[dict[str, Any]] = Field(default_factory=list)
