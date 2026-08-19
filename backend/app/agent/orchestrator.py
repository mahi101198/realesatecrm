"""Call Orchestrator & Context Services for AI Real-Estate Sales CRM."""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.repository import AgentRepository
from app.agent.schemas import AgentPreCallContext
from app.core.exceptions import BusinessRuleError, NotFoundError

logger = logging.getLogger(__name__)

# Enforced Call Job State Transition Matrix
VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"preparing", "cancelled"},
    "scheduled": {"preparing", "cancelled"},
    "preparing": {"ready", "retry_pending", "failed", "cancelled"},
    "ready": {"calling", "cancelled"},
    "calling": {"completed", "retry_pending", "failed", "cancelled"},
    "retry_pending": {"preparing", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


def validate_job_status_transition(current_status: str, new_status: str) -> None:
    """Validate that state transition follows allowed call job state machine."""
    allowed = VALID_STATUS_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise BusinessRuleError(
            message=f"Invalid call job state transition from '{current_status}' to '{new_status}'.",
            code="INVALID_STATE_TRANSITION",
        )


class CallContextService:
    """Service to generate deterministic, snapshot pre-call context for AI agents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = AgentRepository(session)

    async def build_pre_call_context(
        self, tenant_id: UUID | None, lead_id: UUID
    ) -> AgentPreCallContext:
        """Derive authoritative, deterministic pre-call context snapshot."""
        context = await self.repository.get_pre_call_context(tenant_id, lead_id)
        if not context:
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found.",
                code="LEAD_NOT_FOUND",
            )
        return context


class CallOrchestrator:
    """Call job orchestrator managing queue execution, state machine, DNC rules, priority, and concurrency."""

    def __init__(self, session: AsyncSession, max_concurrent_calls: int = 10) -> None:
        self.session = session
        self.repository = AgentRepository(session)
        self.max_concurrent_calls = max_concurrent_calls

    def is_within_calling_window(
        self,
        current_time: datetime | None = None,
        start_hour: int = 9,
        end_hour: int = 20,
    ) -> bool:
        """Verify whether current call attempt falls within allowed local calling hours (09:00 - 20:00 IST)."""
        now = current_time or datetime.now(UTC)
        current_hour = now.hour
        return start_hour <= current_hour < end_hour

    async def create_call_job(
        self,
        tenant_id: UUID,
        lead_id: UUID,
        customer_id: UUID,
        job_type: str = "initial_lead_call",
        priority: int = 5,
        scheduled_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a new call job for dispatching."""
        # 1. Enforce DNC check
        is_dnc = await self.repository.check_do_not_call(tenant_id, customer_id)
        if is_dnc:
            logger.warning(f"Blocked call job creation for DNC customer {customer_id}")
            sql = text(
                """
                INSERT INTO public.call_jobs (
                    tenant_id, lead_id, customer_id, job_type, priority, status,
                    last_error_code, last_error_message
                ) VALUES (
                    :tenant_id, :lead_id, :customer_id, :job_type, :priority, 'cancelled',
                    'DO_NOT_CALL_ACTIVE', 'Customer is marked as Do-Not-Call.'
                )
                RETURNING *
                """
            )
            res = await self.session.execute(
                sql,
                {
                    "tenant_id": tenant_id,
                    "lead_id": lead_id,
                    "customer_id": customer_id,
                    "job_type": job_type,
                    "priority": priority,
                },
            )
            return dict(res.mappings().one())

        # 2. Insert call job
        sched = scheduled_at or datetime.now(UTC)
        sql = text(
            """
            INSERT INTO public.call_jobs (
                tenant_id, lead_id, customer_id, job_type, priority, status, scheduled_at
            ) VALUES (
                :tenant_id, :lead_id, :customer_id, :job_type, :priority, 'queued', :scheduled_at
            )
            RETURNING *
            """
        )
        res = await self.session.execute(
            sql,
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "customer_id": customer_id,
                "job_type": job_type,
                "priority": priority,
                "scheduled_at": sched,
            },
        )
        return dict(res.mappings().one())

    async def get_next_eligible_jobs(
        self, tenant_id: UUID | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Fetch next ready call jobs ordered by priority (higher priority first) and schedule time."""
        sql = text(
            """
            SELECT cj.*, l.lead_score
            FROM public.call_jobs cj
            JOIN public.leads l ON cj.lead_id = l.id
            JOIN public.customers c ON cj.customer_id = c.id
            WHERE cj.status IN ('queued', 'retry_pending')
              AND cj.scheduled_at <= NOW()
              AND cj.attempt_count < cj.max_attempts
              AND c.do_not_call = FALSE
              AND (:tenant_id::uuid IS NULL OR cj.tenant_id = :tenant_id)
            ORDER BY
              cj.priority ASC,
              l.lead_score DESC,
              cj.scheduled_at ASC
            LIMIT :limit
            """
        )
        res = await self.session.execute(sql, {"tenant_id": tenant_id, "limit": limit})
        return [dict(r) for r in res.mappings().all()]

    async def update_job_status(
        self, tenant_id: UUID, call_job_id: UUID, new_status: str, error_code: str | None = None, error_message: str | None = None
    ) -> dict[str, Any]:
        """Safely transition call job status with validation."""
        job = await self.repository.claim_job_for_update(tenant_id, call_job_id)
        if not job:
            raise NotFoundError(message=f"Call job '{call_job_id}' not found.", code="JOB_NOT_FOUND")

        current_status = str(job["status"])
        validate_job_status_transition(current_status, new_status)

        sql = text(
            """
            UPDATE public.call_jobs
            SET status = :new_status::public.call_job_status,
                last_error_code = COALESCE(:error_code, last_error_code),
                last_error_message = COALESCE(:error_message, last_error_message),
                updated_at = NOW()
            WHERE id = :call_job_id AND tenant_id = :tenant_id
            RETURNING *
            """
        )
        res = await self.session.execute(
            sql,
            {
                "call_job_id": call_job_id,
                "tenant_id": tenant_id,
                "new_status": new_status,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        return dict(res.mappings().one())
