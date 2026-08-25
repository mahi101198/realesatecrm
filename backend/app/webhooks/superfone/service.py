"""Superfone Webhook Business Service Layer.

Handles both webhook streams:
  - SFVoPI call events (answer/ring/hangup) -- correlated to `public.calls`
    via provider_call_id, which we set to SFVoPI's request_uuid at call
    initiation and normalize to call_uuid once the first webhook arrives.
  - CRM event notifications (ALL_CALLS/MISSED_CALL/CDR_RECORDING_AVAILABLE/
    CDR_SUMMARY_READY) -- correlated via `cdr_uuid`, a DISTINCT identifier
    space from SFVoPI's call_uuid/request_uuid (two separate Superfone
    subsystems; no documented cross-reference between them). These events
    create/update their own `public.calls` row (provider='superfone_crm'),
    independent of any SFVoPI-tracked call.

Every delivery (matched or not, processed or duplicate) is persisted to
public.webhook_events first, keyed for idempotency on (provider,
external_event_id) -- see uq_webhook_events_provider_external_notnull.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.webhooks.superfone.schemas import StreamConfig, StreamResponse

logger = logging.getLogger(__name__)

_SFVOPI_PROVIDER = "superfone_sfvopi"
_CRM_PROVIDER = "superfone_crm"

# S3 presigned recording URLs are only valid 7 days -- never treat as durable.
_RECORDING_URL_VALID_DAYS = 7


class SuperfoneWebhookService:
    """Service backing the Superfone webhook router. Every public method is
    designed to never raise past the point where the caller needs to send
    its HTTP response -- DB problems are logged, not allowed to block or
    corrupt the webhook acknowledgement."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Idempotency / audit log (shared by both webhook streams)
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
        arbiter and raises 42P10, which would make EVERY delivery on this
        stream fail rather than deduplicate. Kept identical to
        WhatsAppWebhookService._record_event.
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
                # not a dict -- a raw dict raises DataError on every real
                # insert (mocked tests never catch this: they never serialize
                # parameters). Pre-existing bug, found live-testing the voice
                # pipeline against a real database for the first time.
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
    # SFVoPI: answer / ring / hangup
    # ------------------------------------------------------------------

    def build_stream_response(self, payload: dict[str, Any] | None = None) -> StreamResponse:
        """Build the Stream JSON response -- pure, no DB access, so the
        answer handler can return it even if webhook bookkeeping fails.

        The configured URL is a single static value, so on its own it cannot
        tell the media-stream endpoint WHICH call is arriving. When the answer
        payload is available we append `call=<request_uuid|call_uuid>` -- the
        same identifier `AgentGateway._place_sfvopi_call` stored in
        `calls.provider_call_id` -- so `app/voice/router.py` can correlate the
        socket back to a call job without a side channel. `payload` stays
        optional so the pure no-argument form still works.
        """
        url = settings.VOICE_AGENT_STREAM_URL
        correlation = None
        if payload:
            correlation = payload.get("request_uuid") or payload.get("call_uuid")
        if url and correlation:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode({'call': str(correlation)})}"

        return StreamResponse(
            stream=StreamConfig(
                url=url,
                codec=settings.VOICE_AGENT_STREAM_CODEC,
                sample_rate=settings.VOICE_AGENT_STREAM_SAMPLE_RATE,
                direction="BOTH",
                stream_timeout=86400,
            )
        )

    async def handle_sfvopi_answer(self, payload: dict[str, Any]) -> None:
        """Persist + correlate the answer event. Called AFTER the Stream
        JSON response has already been built by the router -- never let a
        DB error here delay or fail that response."""
        call_uuid = payload.get("call_uuid")
        request_uuid = payload.get("request_uuid")
        external_id = f"{call_uuid or request_uuid or 'unknown'}:answer"

        is_new = await self._record_event(
            tenant_id=None,
            provider=_SFVOPI_PROVIDER,
            event_type="answer",
            external_event_id=external_id,
            payload=payload,
        )
        if not is_new:
            return

        row = await self._find_and_normalize_sfvopi_call(call_uuid, request_uuid)
        if row:
            answered_at = _parse_timestamp(payload.get("answered_at")) or datetime.now(UTC)
            await self.session.execute(
                text(
                    """
                    UPDATE public.calls
                    SET status = 'answered'::public.call_status,
                        answered_at = :answered_at,
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": row["id"], "answered_at": answered_at},
            )
        else:
            logger.warning(
                f"SFVoPI answer webhook could not be correlated to a calls row: "
                f"call_uuid={call_uuid} request_uuid={request_uuid}"
            )

        await self._mark_event_processed(_SFVOPI_PROVIDER, external_id)

    async def handle_sfvopi_ring(self, payload: dict[str, Any]) -> None:
        """Persist + correlate the ring event (phone is ringing)."""
        call_uuid = payload.get("call_uuid")
        request_uuid = payload.get("request_uuid")
        external_id = f"{call_uuid or request_uuid or 'unknown'}:ring"

        is_new = await self._record_event(
            tenant_id=None,
            provider=_SFVOPI_PROVIDER,
            event_type="ring",
            external_event_id=external_id,
            payload=payload,
        )
        if not is_new:
            return

        row = await self._find_and_normalize_sfvopi_call(call_uuid, request_uuid)
        if row and row["status"] == "initiated":
            await self.session.execute(
                text(
                    """
                    UPDATE public.calls
                    SET status = 'ringing'::public.call_status, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": row["id"]},
            )

        await self._mark_event_processed(_SFVOPI_PROVIDER, external_id)

    async def handle_sfvopi_hangup(self, payload: dict[str, Any]) -> None:
        """Persist + correlate the hangup event (call ended)."""
        call_uuid = payload.get("call_uuid")
        request_uuid = payload.get("request_uuid")
        external_id = f"{call_uuid or request_uuid or 'unknown'}:hangup"

        is_new = await self._record_event(
            tenant_id=None,
            provider=_SFVOPI_PROVIDER,
            event_type="hangup",
            external_event_id=external_id,
            payload=payload,
        )
        if not is_new:
            return

        row = await self._find_and_normalize_sfvopi_call(call_uuid, request_uuid)
        if row:
            await self.session.execute(
                text(
                    """
                    UPDATE public.calls
                    SET status = 'completed'::public.call_status,
                        ended_at = COALESCE(ended_at, NOW()),
                        duration_seconds = COALESCE(
                            duration_seconds,
                            CASE WHEN answered_at IS NOT NULL
                                 THEN EXTRACT(EPOCH FROM (NOW() - answered_at))::int
                                 ELSE NULL END
                        ),
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": row["id"]},
            )

        await self._mark_event_processed(_SFVOPI_PROVIDER, external_id)

    async def _find_and_normalize_sfvopi_call(
        self, call_uuid: str | None, request_uuid: str | None
    ) -> dict[str, Any] | None:
        """Find the `calls` row this SFVoPI event belongs to, trying
        call_uuid first (the identifier late-stage events carry) and
        falling back to request_uuid (what we stored at initiation, before
        Superfone's call_uuid was known). Normalizes provider_call_id to
        call_uuid once both are known, so later events match directly."""
        row = None
        if call_uuid:
            result = await self.session.execute(
                text(
                    """
                    SELECT id, tenant_id, status, provider_call_id
                    FROM public.calls
                    WHERE provider = :provider AND provider_call_id = :call_uuid
                    """
                ),
                {"provider": _SFVOPI_PROVIDER, "call_uuid": call_uuid},
            )
            row = result.mappings().one_or_none()

        if row is None and request_uuid:
            result = await self.session.execute(
                text(
                    """
                    SELECT id, tenant_id, status, provider_call_id
                    FROM public.calls
                    WHERE provider = :provider AND provider_call_id = :request_uuid
                    """
                ),
                {"provider": _SFVOPI_PROVIDER, "request_uuid": request_uuid},
            )
            row = result.mappings().one_or_none()

        if row is not None and call_uuid and row["provider_call_id"] != call_uuid:
            await self.session.execute(
                text(
                    """
                    UPDATE public.calls
                    SET provider_call_id = :call_uuid,
                        metadata = metadata || CAST(:meta_fragment AS jsonb),
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "call_uuid": call_uuid,
                    "meta_fragment": {"sfvopi_request_uuid": request_uuid},
                },
            )

        return dict(row) if row else None

    # ------------------------------------------------------------------
    # CRM event notifications (CDR lifecycle)
    # ------------------------------------------------------------------

    async def handle_crm_event(
        self, tenant_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Persist + process a CRM event-notification delivery. `tenant_id`
        comes from the URL path (see router.py), verified against this
        tenant's own bearer secret before this is ever called -- it is
        never guessed or inferred from the payload."""
        cdr_uuid = payload.get("cdr_uuid")
        external_id = f"{cdr_uuid or 'unknown'}:{event_type}"

        is_new = await self._record_event(
            tenant_id=tenant_id,
            provider=_CRM_PROVIDER,
            event_type=event_type,
            external_event_id=external_id,
            payload=payload,
        )
        if not is_new:
            return

        try:
            if event_type in ("ALL_CALLS", "MISSED_CALL"):
                await self._upsert_crm_call(tenant_id, event_type, cdr_uuid, payload)
            elif event_type == "CDR_RECORDING_AVAILABLE":
                await self._apply_crm_recording(tenant_id, cdr_uuid, payload)
            elif event_type == "CDR_SUMMARY_READY":
                await self._apply_crm_summary(tenant_id, cdr_uuid, payload)
            else:
                logger.info(f"Ignoring unhandled/competing CRM event type '{event_type}'.")
            await self._mark_event_processed(_CRM_PROVIDER, external_id)
        except Exception as e:
            logger.error(f"Error processing CRM event {external_id}: {e!s}")
            await self._mark_event_processed(_CRM_PROVIDER, external_id, error=str(e))
            # Do not re-raise: the delivery is already durably logged for
            # replay/support, and Superfone does not retry -- swallowing
            # here (after logging) avoids turning a downstream data-mapping
            # bug into a 5xx that teaches Superfone nothing useful.

    async def _get_calls_row_by_cdr_uuid(
        self, tenant_id: UUID, cdr_uuid: str | None
    ) -> dict[str, Any] | None:
        if not cdr_uuid:
            return None
        result = await self.session.execute(
            text(
                """
                SELECT id, tenant_id, metadata FROM public.calls
                WHERE provider = :provider AND provider_call_id = :cdr_uuid
                  AND tenant_id = :tenant_id
                """
            ),
            {"provider": _CRM_PROVIDER, "cdr_uuid": cdr_uuid, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def _upsert_crm_call(
        self, tenant_id: UUID, event_type: str, cdr_uuid: str | None, payload: dict[str, Any]
    ) -> None:
        """Update the `calls` row for a finalized human call CDR, if one is
        already correlated by cdr_uuid for this tenant.

        REMAINING LIMITATION: `calls.customer_id` is NOT NULL, so a `calls`
        row can only ever be created with a real, known customer -- never a
        guessed one. tenant_id is now known (see handle_crm_event), which
        resolves the tenant-isolation half of this gap, but nothing in this
        integration pass resolves cdr_phone to a customer_id, so a CDR event
        with no pre-existing correlated `calls` row still cannot create one.
        That resolution step (phone -> customer, mirroring
        app.customers.resolver.ContactResolver) is a distinct follow-up.
        """
        existing = await self._get_calls_row_by_cdr_uuid(tenant_id, cdr_uuid)
        if not existing:
            logger.warning(
                f"CRM {event_type} for tenant={tenant_id} cdr_uuid={cdr_uuid} has no "
                "correlated calls row (customer cannot be resolved for a cold CDR event) "
                "-- recorded in webhook_events only."
            )
            return

        status = "no_answer" if event_type == "MISSED_CALL" else "completed"
        await self.session.execute(
            text(
                """
                UPDATE public.calls
                SET status = CAST(:status AS public.call_status),
                    duration_seconds = COALESCE(:duration_seconds, duration_seconds),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": existing["id"],
                "status": status,
                "duration_seconds": payload.get("cdr_duration") or payload.get("duration"),
            },
        )

    async def _apply_crm_recording(
        self, tenant_id: UUID, cdr_uuid: str | None, payload: dict[str, Any]
    ) -> None:
        """Store the presigned recording URL. Valid only 7 days -- stored
        with an explicit expiry marker in metadata, never treated as durable.
        No re-hosting/download worker is built in this pass (out of scope)."""
        existing = await self._get_calls_row_by_cdr_uuid(tenant_id, cdr_uuid)
        if not existing:
            logger.warning(
                f"CDR_RECORDING_AVAILABLE for unknown cdr_uuid={cdr_uuid}; nothing to update."
            )
            return

        recording_url = payload.get("cdr_recording_url")
        recording_status = payload.get("cdr_recording_status")
        expires_at = datetime.now(UTC).isoformat()
        await self.session.execute(
            text(
                """
                UPDATE public.calls
                SET recording_url = :recording_url,
                    metadata = metadata || CAST(:meta_fragment AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": existing["id"],
                "recording_url": recording_url,
                "meta_fragment": {
                    "cdr_recording_status": recording_status,
                    "cdr_recording_url_fetched_at": expires_at,
                    "cdr_recording_url_valid_days": _RECORDING_URL_VALID_DAYS,
                    "cdr_recording_url_presigned_expiry_warning": (
                        "This is a presigned S3 URL valid for only "
                        f"{_RECORDING_URL_VALID_DAYS} days from issuance -- not durable."
                    ),
                },
            },
        )

    async def _apply_crm_summary(
        self, tenant_id: UUID, cdr_uuid: str | None, payload: dict[str, Any]
    ) -> None:
        """Store the AI-generated call summary/insights/transcript. This
        event carries NO contact/task/staff fields (cdr_phone is null per
        Superfone's docs), so it can only ever update an existing row."""
        existing = await self._get_calls_row_by_cdr_uuid(tenant_id, cdr_uuid)
        if not existing:
            logger.warning(
                f"CDR_SUMMARY_READY for unknown cdr_uuid={cdr_uuid}; nothing to update."
            )
            return

        summary = payload.get("summary") or {}
        summary_text = payload.get("summary_text")
        transcription = payload.get("transcription")
        await self.session.execute(
            text(
                """
                UPDATE public.calls
                SET call_summary = COALESCE(:summary_text, call_summary),
                    metadata = metadata || CAST(:meta_fragment AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": existing["id"],
                "summary_text": summary_text,
                "meta_fragment": {
                    "cdr_insights": summary.get("insights") if isinstance(summary, dict) else None,
                    "cdr_transcription": transcription,
                },
            },
        )


def _parse_timestamp(value: Any) -> datetime | None:
    """Best-effort ISO-8601 timestamp parse; never raises."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
