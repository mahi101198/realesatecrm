"""Agent Gateway Abstraction Boundary for AI Real-Estate Sales CRM.

Translates between CRM business logic/orchestration and external AI agent runtimes.
Does not contain vendor-specific SDK calls (LiveKit/Twilio). Superfone SFVoPI
calls go through app.integrations.superfone.client.SFVoPIClient's clean
interface only -- this module never touches httpx/Superfone HTTP shapes
directly (skills/system.md rule #31).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import (
    CallContextService,
    CallOrchestrator,
    validate_job_status_transition,
)
from app.agent.repository import AgentRepository
from app.core.config import settings
from app.core.constants import API_V1_PREFIX
from app.core.exceptions import AppError, ForbiddenError, NotFoundError
from app.integrations.superfone.factory import get_sfvopi_client

logger = logging.getLogger(__name__)

# Outcomes that trigger auto-retry policy if attempts remaining
RETRYABLE_OUTCOMES = {"no_answer", "busy", "technical_failure"}

# Terminal outcomes that finalize call job
TERMINAL_OUTCOMES = {
    "connected",
    "customer_requested_callback",
    "customer_not_interested",
    "site_visit_scheduled",
    "human_transfer",
    "wrong_number",
    "rejected",
}


class AgentGateway:
    """Boundary abstraction for managing AI agent call lifecycles, context snapshots, and completions."""

    def __init__(self, session: AsyncSession, max_concurrent_calls: int = 10) -> None:
        self.session = session
        self.repository = AgentRepository(session)
        self.orchestrator = CallOrchestrator(session, max_concurrent_calls)
        self.context_service = CallContextService(session)

    async def prepare_call(self, tenant_id: UUID, call_job_id: UUID) -> dict[str, Any]:
        """Prepare call job: Build deterministic snapshot context and transition job queued -> preparing -> ready."""
        job = await self.repository.claim_job_for_update(tenant_id, call_job_id)
        if not job:
            raise NotFoundError(message=f"Call job '{call_job_id}' not found.", code="JOB_NOT_FOUND")

        current_status = str(job["status"])
        validate_job_status_transition(current_status, "preparing")

        # Update status to preparing
        await self.orchestrator.update_job_status(tenant_id, call_job_id, "preparing")

        try:
            # Build pre-call context snapshot
            pre_call_context = await self.context_service.build_pre_call_context(
                tenant_id, job["lead_id"]
            )
            snapshot_dict = pre_call_context.model_dump(mode="json")

            # Save snapshot in agent_call_contexts
            snapshot_row = await self.repository.save_agent_call_context_snapshot(
                tenant_id=tenant_id,
                call_job_id=call_job_id,
                lead_id=job["lead_id"],
                customer_id=job["customer_id"],
                context_payload=snapshot_dict,
            )

            # Transition status preparing -> ready
            ready_job = await self.orchestrator.update_job_status(tenant_id, call_job_id, "ready")

            return {
                "job": ready_job,
                "context_snapshot_id": str(snapshot_row["id"]),
                "pre_call_context": snapshot_dict,
            }
        except Exception as e:
            logger.error(f"Error preparing call job {call_job_id}: {e!s}")
            await self.orchestrator.update_job_status(
                tenant_id,
                call_job_id,
                "retry_pending",
                error_code="PREPARATION_FAILED",
                error_message=str(e),
            )
            raise

    async def start_call(self, tenant_id: UUID, call_job_id: UUID) -> dict[str, Any]:
        """Start call attempt: Re-check DNC, check calling window & concurrency, transition ready -> calling."""
        job = await self.repository.claim_job_for_update(tenant_id, call_job_id)
        if not job:
            raise NotFoundError(message=f"Call job '{call_job_id}' not found.", code="JOB_NOT_FOUND")

        current_status = str(job["status"])
        validate_job_status_transition(current_status, "calling")

        # 1. RE-CHECK DNC AT CALL START (Section 12)
        is_dnc = await self.repository.check_do_not_call(tenant_id, job["customer_id"])
        if is_dnc:
            logger.warning(f"Call start blocked by DNC re-check for call job {call_job_id}")
            cancelled_job = await self.orchestrator.update_job_status(
                tenant_id,
                call_job_id,
                "cancelled",
                error_code="DO_NOT_CALL_ACTIVE",
                error_message="Call start blocked by active Do-Not-Call preference.",
            )
            return {
                "success": False,
                "error_code": "DO_NOT_CALL_ACTIVE",
                "message": "Call blocked: Customer is marked as Do-Not-Call.",
                "job": cancelled_job,
            }

        # 2. CHECK CALLING WINDOW (Section 13)
        if not self.orchestrator.is_within_calling_window():
            return {
                "success": False,
                "error_code": "OUTSIDE_CALLING_WINDOW",
                "message": "Call start blocked: Current time is outside allowed calling hours (09:00-20:00 IST).",
                "job": dict(job),
            }

        # 3. CHECK TENANT CONCURRENCY LIMITS
        active_query = text(
            """
            SELECT COUNT(id) AS active_calls
            FROM public.call_jobs
            WHERE tenant_id = :tenant_id AND status = 'calling'
            """
        )
        act_res = await self.session.execute(active_query, {"tenant_id": tenant_id})
        active_count = act_res.mappings().one()["active_calls"]

        if active_count >= self.orchestrator.max_concurrent_calls:
            raise ForbiddenError(
                message=f"Tenant concurrency limit of {self.orchestrator.max_concurrent_calls} active calls reached.",
                code="CONCURRENCY_LIMIT_REACHED",
            )

        # 4. TRANSITION TO CALLING
        upd_job_sql = text(
            """
            UPDATE public.call_jobs
            SET status = 'calling', started_at = NOW(), attempt_count = attempt_count + 1, updated_at = NOW()
            WHERE id = :call_job_id AND tenant_id = :tenant_id
            RETURNING *
            """
        )
        upd_res = await self.session.execute(
            upd_job_sql, {"call_job_id": call_job_id, "tenant_id": tenant_id}
        )
        job_row = dict(upd_res.mappings().one())

        # 5. CREATE CALL ATTEMPT LOG
        att_sql = text(
            """
            INSERT INTO public.call_attempts (
                tenant_id, lead_id, customer_id, call_job_id, attempt_number, status, started_at
            ) VALUES (
                :tenant_id, :lead_id, :customer_id, :call_job_id, :attempt_number, 'in_progress', NOW()
            )
            RETURNING *
            """
        )
        att_res = await self.session.execute(
            att_sql,
            {
                "tenant_id": tenant_id,
                "lead_id": job_row["lead_id"],
                "customer_id": job_row["customer_id"],
                "call_job_id": call_job_id,
                "attempt_number": job_row["attempt_count"],
            },
        )
        att_row = dict(att_res.mappings().one())

        # 6. PLACE THE OUTBOUND CALL VIA SUPERFONE SFVOPI
        placement = await self._place_sfvopi_call(tenant_id, job_row, att_row)
        if not placement["success"]:
            return placement

        return {"success": True, "job": job_row, "attempt": placement["attempt"]}

    async def _place_sfvopi_call(
        self, tenant_id: UUID, job_row: dict[str, Any], att_row: dict[str, Any]
    ) -> dict[str, Any]:
        """Place the outbound AI call via Superfone SFVoPI and record the
        result. On success, creates the `calls` row (the rich telephony/AI-
        metrics record webhooks later update) and links it from `call_jobs`.
        On failure, reuses record_call_completed's existing retry-policy
        machinery instead of duplicating it -- a failed placement is treated
        as a 'technical_failure' outcome, same as any other call failure."""
        customer_res = await self.session.execute(
            text(
                "SELECT phone FROM public.customers WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": job_row["customer_id"], "tenant_id": tenant_id},
        )
        customer_row = customer_res.mappings().one_or_none()
        from_number = settings.SUPERFONE_SFVOPI_FROM_NUMBER
        to_number = customer_row["phone"] if customer_row else None

        if not from_number or not to_number:
            return await self._fail_placement(
                tenant_id,
                job_row,
                att_row,
                failure_code="SFVOPI_MISSING_PHONE_CONFIG",
                failure_message=(
                    "Missing outbound caller ID (SUPERFONE_SFVOPI_FROM_NUMBER) or "
                    "customer phone number; cannot place SFVoPI call."
                ),
            )

        webhook_token = settings.SUPERFONE_WEBHOOK_SHARED_SECRET.get_secret_value()
        webhook_base = f"{settings.APP_PUBLIC_BASE_URL}{API_V1_PREFIX}/webhooks/superfone/sfvopi"
        query = urlencode({"token": webhook_token})

        try:
            client = get_sfvopi_client()
            result = await client.initiate_outbound_call(
                from_number=from_number,
                to_number=to_number,
                answer_url=f"{webhook_base}/answer?{query}",
                ring_url=f"{webhook_base}/ring?{query}",
                hangup_url=f"{webhook_base}/hangup?{query}",
            )
        except AppError as e:
            logger.error(f"SFVoPI outbound call placement failed: {e.code}: {e.message}")
            return await self._fail_placement(
                tenant_id, job_row, att_row, failure_code=e.code, failure_message=e.message
            )

        request_uuid = result["request_uuid"]

        calls_res = await self.session.execute(
            text(
                """
                INSERT INTO public.calls (
                    tenant_id, lead_id, customer_id, agent_config_id,
                    direction, provider, provider_call_id, phone_from, phone_to, status
                ) VALUES (
                    :tenant_id, :lead_id, :customer_id, NULL,
                    'outbound', 'superfone_sfvopi', :request_uuid, :from_number, :to_number,
                    'initiated'::public.call_status
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "lead_id": job_row["lead_id"],
                "customer_id": job_row["customer_id"],
                "request_uuid": request_uuid,
                "from_number": from_number,
                "to_number": to_number,
            },
        )
        calls_id = calls_res.mappings().one()["id"]

        await self.session.execute(
            text(
                "UPDATE public.call_jobs SET call_id = :calls_id, updated_at = NOW() "
                "WHERE id = :call_job_id AND tenant_id = :tenant_id"
            ),
            {"calls_id": calls_id, "call_job_id": job_row["id"], "tenant_id": tenant_id},
        )

        att_res = await self.session.execute(
            text(
                "UPDATE public.call_attempts SET provider_call_id = :request_uuid, "
                "updated_at = NOW() WHERE id = :id RETURNING *"
            ),
            {"request_uuid": request_uuid, "id": att_row["id"]},
        )
        updated_att_row = dict(att_res.mappings().one())

        return {"success": True, "attempt": updated_att_row, "call_id": str(calls_id)}

    async def _fail_placement(
        self,
        tenant_id: UUID,
        job_row: dict[str, Any],
        att_row: dict[str, Any],
        *,
        failure_code: str,
        failure_message: str,
    ) -> dict[str, Any]:
        """Record a failed call-placement attempt using the existing retry-policy
        machinery in record_call_completed, rather than duplicating it here."""
        completion = await self.record_call_completed(
            tenant_id=tenant_id,
            call_job_id=job_row["id"],
            call_attempt_id=att_row["id"],
            outcome="technical_failure",
            failure_code=failure_code,
            failure_message=failure_message,
        )
        return {
            "success": False,
            "error_code": failure_code,
            "message": failure_message,
            "job": completion["job"],
            "attempt": completion["attempt"],
        }

    async def record_call_completed(
        self,
        tenant_id: UUID,
        call_job_id: UUID,
        call_attempt_id: UUID,
        outcome: str,
        duration_seconds: int | None = None,
        call_summary: str | None = None,
        provider_call_id: str | None = None,
        termination_reason: str | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> dict[str, Any]:
        """Record call completion, update attempt record, apply retry policy, and transition call job state."""
        job = await self.repository.claim_job_for_update(tenant_id, call_job_id)
        if not job:
            raise NotFoundError(message=f"Call job '{call_job_id}' not found.", code="JOB_NOT_FOUND")

        # 1. Update call_attempts record
        att_sql = text(
            """
            UPDATE public.call_attempts
            SET status = 'completed',
                outcome = :outcome::public.call_attempt_outcome,
                ended_at = NOW(),
                duration_seconds = :duration_seconds,
                provider_call_id = COALESCE(:provider_call_id, provider_call_id),
                termination_reason = :termination_reason,
                failure_code = :failure_code,
                failure_message = :failure_message,
                updated_at = NOW()
            WHERE id = :call_attempt_id AND tenant_id = :tenant_id
            RETURNING *
            """
        )
        att_res = await self.session.execute(
            att_sql,
            {
                "call_attempt_id": call_attempt_id,
                "tenant_id": tenant_id,
                "outcome": outcome,
                "duration_seconds": duration_seconds,
                "provider_call_id": provider_call_id,
                "termination_reason": termination_reason,
                "failure_code": failure_code,
                "failure_message": failure_message,
            },
        )
        att_row = att_res.mappings().one_or_none()

        # 2. Determine call job next state based on retry policy
        attempt_count = job["attempt_count"]
        max_attempts = job["max_attempts"]

        if outcome in RETRYABLE_OUTCOMES and attempt_count < max_attempts:
            next_status = "retry_pending"
            next_attempt_at = datetime.now(UTC) + timedelta(hours=2)
            error_code = failure_code or f"RETRYABLE_OUTCOME_{outcome.upper()}"
            error_msg = failure_message or f"Call outcome '{outcome}' queued for retry attempt {attempt_count + 1}."
        elif outcome in RETRYABLE_OUTCOMES:
            next_status = "failed"
            next_attempt_at = None
            error_code = failure_code or "MAX_ATTEMPTS_EXCEEDED"
            error_msg = f"Maximum attempts ({max_attempts}) reached for call job."
        else:
            next_status = "completed"
            next_attempt_at = None
            error_code = None
            error_msg = None

        # Update call_jobs
        validate_job_status_transition(str(job["status"]), next_status)
        job_sql = text(
            """
            UPDATE public.call_jobs
            SET status = :next_status::public.call_job_status,
                completed_at = CASE WHEN :next_status = 'completed' THEN NOW() ELSE completed_at END,
                next_attempt_at = :next_attempt_at,
                last_error_code = :error_code,
                last_error_message = :error_msg,
                updated_at = NOW()
            WHERE id = :call_job_id AND tenant_id = :tenant_id
            RETURNING *
            """
        )
        job_res = await self.session.execute(
            job_sql,
            {
                "call_job_id": call_job_id,
                "tenant_id": tenant_id,
                "next_status": next_status,
                "next_attempt_at": next_attempt_at,
                "error_code": error_code,
                "error_msg": error_msg,
            },
        )
        job_row = dict(job_res.mappings().one())

        return {
            "job": job_row,
            "attempt": dict(att_row) if att_row else None,
            "next_status": next_status,
        }

    async def reconcile_stuck_jobs(
        self, tenant_id: UUID | None = None, timeout_minutes: int = 15
    ) -> list[dict[str, Any]]:
        """Find orphaned active jobs exceeding timeout and transition them safely."""
        return await self.repository.find_and_reconcile_stuck_jobs(
            tenant_id=tenant_id, timeout_minutes=timeout_minutes
        )
