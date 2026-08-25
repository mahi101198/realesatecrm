"""Domain event log (deterministic foundation layer).

DB-only in this phase: `publish_event` appends a row to `public.events` in the
same session as the state change it describes. There is no bus, no subscriber
and no dispatch -- a later phase can add those on top without touching any of
the call sites.
"""

from app.events.model import EventType
from app.events.publisher import publish_event

__all__ = ["EventType", "publish_event"]
