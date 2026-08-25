"""Coercion of model-supplied tool arguments into the types tools declare.

Shared by every agent that hands `TOOL_REGISTRY` handlers arguments the model
chose. It lived inside the WhatsApp agent until the voice agent needed exactly
the same behaviour; a second copy would have been two allowlists of id names
free to drift apart, so it moved here instead.

Nothing in this module touches the database or makes a decision -- it is purely
"JSON says string, the tool signature says UUID".
"""

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Every argument name in the registered tool surface that is annotated `UUID`.
UUID_ARGUMENT_NAMES = ("property_id", "project_id", "lead_id", "customer_id")


def coerce_uuid_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Convert known id arguments from strings to `UUID`.

    Model tool inputs arrive as JSON, so UUIDs arrive as strings while the tool
    signatures are annotated `UUID`. Unparseable ids are DROPPED rather than
    passed through, so a malformed id becomes "the tool was called without that
    argument" instead of a 500 deep inside a service.
    """
    coerced: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in UUID_ARGUMENT_NAMES and isinstance(value, str):
            try:
                coerced[key] = UUID(value)
            except ValueError:
                logger.warning(f"Agent supplied a malformed UUID for {key!r}; dropping.")
                continue
        else:
            coerced[key] = value
    return coerced
