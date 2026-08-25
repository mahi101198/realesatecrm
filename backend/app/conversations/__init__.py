"""Conversations -- the durable thread a contact has with a tenant on one channel.

Minimal by design in this phase: a model and a race-safe
`get_or_create_conversation`, just enough for other code to obtain a
`conversation_id`. No service, no router, no CRUD -- those arrive (if ever
needed) with the phase that actually reads conversations.
"""

from app.conversations.model import Conversation, ConversationChannel, ConversationStatus
from app.conversations.repository import ConversationRepository, get_or_create_conversation

__all__ = [
    "Conversation",
    "ConversationChannel",
    "ConversationRepository",
    "ConversationStatus",
    "get_or_create_conversation",
]
