"""Unit tests for SuperfoneWebhookService: idempotency, SFVoPI call
correlation (call_uuid/request_uuid normalization), and CRM CDR event
handling (including the documented tenant-resolution limitation for
first-time/unmatched CDR events)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.webhooks.superfone.service import SuperfoneWebhookService


def _service() -> tuple[SuperfoneWebhookService, AsyncMock]:
    session = AsyncMock()
    return SuperfoneWebhookService(session), session


# ---------------------------------------------------------------------------
# Idempotency (public.webhook_events)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_event_returns_true_for_new_event() -> None:
    """Verify a fresh delivery (INSERT ... RETURNING id succeeds) is treated
    as new and eligible for processing."""
    service, session = _service()
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    session.execute.return_value = result

    is_new = await service._record_event(
        tenant_id=None,
        provider="superfone_sfvopi",
        event_type="answer",
        external_event_id="call123:answer",
        payload={"call_uuid": "call123"},
    )
    assert is_new is True


@pytest.mark.asyncio
async def test_record_event_returns_false_for_duplicate_delivery() -> None:
    """Verify a redelivered event (ON CONFLICT DO NOTHING -> no row) is
    treated as a duplicate no-op, not reprocessed."""
    service, session = _service()
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = None
    session.execute.return_value = result

    is_new = await service._record_event(
        tenant_id=None,
        provider="superfone_sfvopi",
        event_type="answer",
        external_event_id="call123:answer",
        payload={"call_uuid": "call123"},
    )
    assert is_new is False


# ---------------------------------------------------------------------------
# SFVoPI call correlation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_sfvopi_answer_skips_processing_for_duplicate() -> None:
    """Verify a duplicate answer delivery does not attempt call correlation."""
    service, session = _service()
    service._record_event = AsyncMock(return_value=False)  # type: ignore[method-assign]
    find_mock = AsyncMock()
    service._find_and_normalize_sfvopi_call = find_mock  # type: ignore[method-assign]

    await service.handle_sfvopi_answer({"call_uuid": "call123", "request_uuid": "req456"})
    find_mock.assert_not_called()


@pytest.mark.asyncio
async def test_handle_sfvopi_answer_updates_matched_call_to_answered() -> None:
    """Verify a matched call is updated to status='answered'."""
    service, session = _service()
    service._record_event = AsyncMock(return_value=True)  # type: ignore[method-assign]
    call_id = uuid4()
    service._find_and_normalize_sfvopi_call = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": call_id, "tenant_id": uuid4(), "status": "initiated"}
    )
    service._mark_event_processed = AsyncMock()  # type: ignore[method-assign]

    await service.handle_sfvopi_answer(
        {"call_uuid": "call123", "request_uuid": "req456", "answered_at": "2026-01-01T00:00:00Z"}
    )

    # An UPDATE was issued against the matched call.
    assert session.execute.await_count >= 1
    service._mark_event_processed.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_sfvopi_answer_logs_and_continues_when_unmatched() -> None:
    """Verify an unmatched answer event (no correlated calls row) does not
    raise -- it's logged and the event is still marked processed."""
    service, session = _service()
    service._record_event = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service._find_and_normalize_sfvopi_call = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._mark_event_processed = AsyncMock()  # type: ignore[method-assign]

    await service.handle_sfvopi_answer({"call_uuid": "unknown_call"})
    service._mark_event_processed.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_and_normalize_sfvopi_call_falls_back_to_request_uuid() -> None:
    """Verify correlation tries call_uuid first, then falls back to
    request_uuid when call_uuid hasn't been seen yet (e.g. a ring event
    arriving before we've learned/stored the call_uuid)."""
    service, session = _service()
    call_id = uuid4()

    no_match = MagicMock()
    no_match.mappings.return_value.one_or_none.return_value = None
    match_by_request_uuid = MagicMock()
    match_by_request_uuid.mappings.return_value.one_or_none.return_value = {
        "id": call_id,
        "tenant_id": uuid4(),
        "status": "initiated",
        "provider_call_id": "req456",
    }
    normalize_update = MagicMock()

    session.execute.side_effect = [no_match, match_by_request_uuid, normalize_update]

    row = await service._find_and_normalize_sfvopi_call("call123", "req456")

    assert row is not None
    assert row["id"] == call_id
    # 3 calls: try call_uuid (miss), try request_uuid (hit), normalize provider_call_id.
    assert session.execute.await_count == 3


@pytest.mark.asyncio
async def test_find_and_normalize_sfvopi_call_returns_none_when_no_match() -> None:
    """Verify no correlated row returns None rather than raising."""
    service, session = _service()
    no_match = MagicMock()
    no_match.mappings.return_value.one_or_none.return_value = None
    session.execute.return_value = no_match

    row = await service._find_and_normalize_sfvopi_call("call123", "req456")
    assert row is None


# ---------------------------------------------------------------------------
# CRM CDR events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_crm_event_ignores_competing_event_types() -> None:
    """Verify CUSTOMER_CREATE/TASK_* style events are logged and acked, never processed."""
    service, session = _service()
    service._record_event = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service._mark_event_processed = AsyncMock()  # type: ignore[method-assign]

    await service.handle_crm_event("CUSTOMER_CREATE", {"cdr_uuid": "cdr1"})
    service._mark_event_processed.assert_awaited_once()
    # No calls-table mutation attempted for an ignored event type.
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_crm_call_is_a_noop_when_unmatched() -> None:
    """Verify the documented limitation: a cold CDR event (no pre-existing
    calls row correlated by cdr_uuid) is NOT force-inserted with a guessed
    tenant_id, since calls.tenant_id/customer_id are both NOT NULL and must
    never be fabricated. It's recorded in webhook_events only."""
    service, session = _service()
    service._get_calls_row_by_cdr_uuid = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await service._upsert_crm_call("ALL_CALLS", "cdr-unmatched", {"cdr_duration": 42})

    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_crm_call_updates_existing_matched_row() -> None:
    """Verify a matched CDR event updates the existing calls row."""
    service, session = _service()
    call_id = uuid4()
    service._get_calls_row_by_cdr_uuid = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": call_id, "tenant_id": uuid4(), "metadata": {}}
    )

    await service._upsert_crm_call("MISSED_CALL", "cdr-matched", {"cdr_duration": 0})

    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_crm_recording_noop_when_unmatched() -> None:
    """Verify CDR_RECORDING_AVAILABLE for an unknown cdr_uuid does not raise."""
    service, session = _service()
    service._get_calls_row_by_cdr_uuid = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await service._apply_crm_recording("cdr-unmatched", {"cdr_recording_url": "https://s3/x"})
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_apply_crm_recording_stores_url_with_expiry_metadata() -> None:
    """Verify the recording URL is stored WITH an explicit expiry marker,
    never treated as a durable/permanent link (presigned S3 URL, 7-day validity)."""
    service, session = _service()
    call_id = uuid4()
    service._get_calls_row_by_cdr_uuid = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": call_id, "tenant_id": uuid4(), "metadata": {}}
    )

    await service._apply_crm_recording(
        "cdr-matched", {"cdr_recording_url": "https://s3/x", "cdr_recording_status": "ready"}
    )

    session.execute.assert_awaited_once()
    call_kwargs = session.execute.call_args.args[1]
    assert call_kwargs["recording_url"] == "https://s3/x"
    assert "cdr_recording_url_valid_days" in call_kwargs["meta_fragment"]


@pytest.mark.asyncio
async def test_apply_crm_summary_noop_when_unmatched() -> None:
    """Verify CDR_SUMMARY_READY for an unknown cdr_uuid does not raise
    (this event carries no contact/staff fields to fall back on)."""
    service, session = _service()
    service._get_calls_row_by_cdr_uuid = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await service._apply_crm_summary("cdr-unmatched", {"summary_text": "great call"})
    session.execute.assert_not_called()
