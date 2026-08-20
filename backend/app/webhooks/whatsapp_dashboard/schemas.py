"""Request schema for the whatsapp-dashboard call-agent trigger. Shape is
fixed by the (unmodified, external) dashboard product's stub
triggerCallAgent -- this backend cannot dictate it."""

from pydantic import BaseModel


class CallAgentTriggerRequest(BaseModel):
    """{"phone": ..., "reason": ...} exactly as posted by
    whatsapp_busness_dashboard's lib/ai/tools.ts::triggerCallAgent."""

    phone: str
    reason: str
