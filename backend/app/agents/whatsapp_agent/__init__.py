"""WhatsApp conversational agent -- composes and sends outbound replies.

Invoked BY the orchestrator; never the other way round. See
`app.agents.whatsapp_agent.agent` for the full contract.
"""

from app.agents.whatsapp_agent.agent import MAX_REPLY_CHARS, WhatsAppAgent

__all__ = ["MAX_REPLY_CHARS", "WhatsAppAgent"]
