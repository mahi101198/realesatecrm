"""Schemas for Superfone webhook payloads/responses.

Inbound webhook BODIES are deliberately NOT modeled as strict Pydantic
request schemas: Superfone's own documentation confirms the ring/hangup
payload shape isn't fully specified, and the CRM event-notification payload
varies per event type. Strict validation here would turn "a field we didn't
anticipate" into a 422 that Superfone (which does not retry) would silently
drop. Instead, routes accept a raw `dict[str, Any]` body and the service
layer extracts known fields defensively with `.get(...)`.

What IS modeled strictly: the Stream JSON response the answer webhook must
return -- that shape is fully documented and we own producing it correctly.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# CRM event types we actively process. CUSTOMER_CREATE/CUSTOMER_CHANGE/TASK_*
# are Superfone's own competing CRM concepts -- explicitly ignored, per this
# project's data-ownership principle (leads/customers stay authoritative here).
HANDLED_CRM_EVENT_TYPES = frozenset(
    {"ALL_CALLS", "MISSED_CALL", "CDR_RECORDING_AVAILABLE", "CDR_SUMMARY_READY"}
)
IGNORED_CRM_EVENT_TYPES = frozenset(
    {"CUSTOMER_CREATE", "CUSTOMER_CHANGE", "TASK_CREATE", "TASK_UPDATE", "TASK_COMPLETE"}
)


class StreamConfig(BaseModel):
    """The `stream` object inside the Stream JSON response."""

    url: str
    codec: str = "PCMA"
    sample_rate: int = Field(default=8000, alias="sample_rate")
    direction: Literal["BOTH", "INBOUND", "OUTBOUND"] = "BOTH"
    stream_timeout: int = 86400
    extra_headers: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class StreamResponse(BaseModel):
    """Response the answer_url webhook must return within 10 seconds,
    telling Superfone where to open the bidirectional media WebSocket."""

    stream: StreamConfig
