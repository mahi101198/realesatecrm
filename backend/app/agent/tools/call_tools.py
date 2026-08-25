"""Call action tools -- the single code path that places or queues a call.

Before this module there were three places that knew how to dial: the HTTP
endpoints in `app/agent/router.py`, the whatsapp-dashboard trigger service, and
the background `app/workers/call_scheduler.py`. All three re-implemented the
same `create_call_job -> prepare_call -> start_call` sequence, so a change to
call placement had to be made three times or not at all.

Now:

    make_instant_call(...)   -> the ONE function that actually places a call
    schedule_call(...)       -> persists a call_job with scheduled_at; NEVER
                                executes anything

`schedule_call` deliberately contains no execution logic whatsoever. The
background scheduler picks the job up when it is due and dispatches it through
`make_instant_call`, so a scheduled call and an instant call travel the exact
same code path from the moment they are dispatched.

TENANT SCOPING: `tenant_id` is required by both functions and is passed through
to `CallOrchestrator` / `AgentGateway`, every one of whose queries already
carries `WHERE tenant_id = :tenant_id`. The `*_tool` registry wrappers resolve
the tenant from the authenticated `RequestContext` and never from arguments.
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.gateway import AgentGateway
from app.agent.orchestrator import CallOrchestrator
from app.core.exceptions import AppError
from app.core.permissions import Permission, check_permission, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.events.model import EventType
from app.events.publisher import publish_event

logger = logging.getLogger(__name__)

DEFAULT_JOB_TYPE = "initial_lead_call"
INSTANT_CALL_PRIORITY = 1
SCHEDULED_CALL_PRIORITY = 5


def _success_response(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}


def _error_response(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error_code": code, "message": message}


# ---------------------------------------------------------------------------
# Core contract -- callable from workers, services and tool wrappers alike
# ---------------------------------------------------------------------------


async def make_instant_call(
    tenant_id: UUID,
    contact_id: UUID,
    lead_id: UUID,
    phone_number: str | None,
    context: dict[str, Any],
    *,
    session: AsyncSession,
    call_job_id: UUID | None = None,
) -> dict[str, Any]:
    """Place a call for this contact/lead NOW. The only call-placing path.

    Positional arguments are the platform's fixed contract
    `(tenant_id, contact_id, lead_id, phone_number, context)`.

    `session` is keyword-only and required: every caller in this codebase
    already owns a session with its own commit/rollback boundary (FastAPI's
    request session, the worker's `_session_scope`), and this function must
    join that transaction rather than opening a competing one.

    `call_job_id` is keyword-only and optional:
      * omitted  -> a `call_jobs` row is created first (the trigger/tool path).
      * supplied -> that already-persisted job is dispatched, no second row
                    (the scheduler path: the worker has already claimed the
                    job from the queue).

    `phone_number` is accepted for the contract's sake and recorded on the
    emitted event, but it is NOT used to dial: `AgentGateway._place_sfvopi_call`
    reads the number straight off the tenant-scoped `customers` row, which is
    the authoritative value and cannot be spoofed by a caller.

    Returns the gateway's `start_call` result dict, plus `call_job_id`.
    """
    orchestrator = CallOrchestrator(session)

    if call_job_id is None:
        job = await orchestrator.create_call_job(
            tenant_id=tenant_id,
            lead_id=lead_id,
            customer_id=contact_id,
            job_type=str(context.get("job_type") or DEFAULT_JOB_TYPE),
            priority=int(context.get("priority") or INSTANT_CALL_PRIORITY),
        )
        call_job_id = job["id"]

        # DNC is enforced inside create_call_job, which parks the job as
        # 'cancelled' rather than raising. Do not try to dispatch that.
        if str(job.get("status")) == "cancelled":
            await publish_event(
                session,
                tenant_id=tenant_id,
                event_type=EventType.CALL_FAILED,
                contact_id=contact_id,
                lead_id=lead_id,
                conversation_id=context.get("conversation_id"),
                payload={
                    "call_job_id": str(call_job_id),
                    "error_code": str(job.get("last_error_code")),
                    "reason": "blocked_before_dispatch",
                },
            )
            return {
                "success": False,
                "error_code": str(job.get("last_error_code") or "CALL_JOB_CANCELLED"),
                "message": str(job.get("last_error_message") or "Call job was cancelled."),
                "call_job_id": str(call_job_id),
                "job": job,
            }

    await publish_event(
        session,
        tenant_id=tenant_id,
        event_type=EventType.CALL_REQUESTED,
        contact_id=contact_id,
        lead_id=lead_id,
        conversation_id=context.get("conversation_id"),
        payload={
            "call_job_id": str(call_job_id),
            "phone_number": phone_number,
            "reason": context.get("reason"),
            "source": context.get("source", "make_instant_call"),
        },
    )

    gateway = AgentGateway(session)
    await gateway.prepare_call(tenant_id, call_job_id)
    result = await gateway.start_call(tenant_id, call_job_id)
    result["call_job_id"] = str(call_job_id)
    return result


async def schedule_call(
    tenant_id: UUID,
    contact_id: UUID,
    lead_id: UUID,
    phone_number: str | None,
    scheduled_at: datetime | None,
    context: dict[str, Any],
    *,
    session: AsyncSession,
) -> dict[str, Any]:
    """Persist a `call_jobs` row due at `scheduled_at`. Places NO call.

    This function contains zero call-execution logic on purpose. The background
    scheduler claims the job once `scheduled_at <= now()` and dispatches it via
    `make_instant_call`, so there is exactly one code path that dials.

    `scheduled_at=None` means "as soon as the scheduler picks it up" -- the
    same default `CallOrchestrator.create_call_job` already applies.
    """
    orchestrator = CallOrchestrator(session)
    job = await orchestrator.create_call_job(
        tenant_id=tenant_id,
        lead_id=lead_id,
        customer_id=contact_id,
        job_type=str(context.get("job_type") or DEFAULT_JOB_TYPE),
        priority=int(context.get("priority") or SCHEDULED_CALL_PRIORITY),
        scheduled_at=scheduled_at,
    )

    await publish_event(
        session,
        tenant_id=tenant_id,
        event_type=EventType.CALL_SCHEDULED,
        contact_id=contact_id,
        lead_id=lead_id,
        conversation_id=context.get("conversation_id"),
        payload={
            "call_job_id": str(job["id"]),
            "phone_number": phone_number,
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
            "status": str(job.get("status")),
            "reason": context.get("reason"),
            "source": context.get("source", "schedule_call"),
        },
    )
    return job


# ---------------------------------------------------------------------------
# Registry wrappers -- same shape as read_tools.py / write_tools.py
# ---------------------------------------------------------------------------


async def make_instant_call_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    customer_id: UUID,
    phone_number: str | None = None,
    reason: str | None = None,
    conversation_id: UUID | None = None,
) -> dict[str, Any]:
    """AI Tool: place an immediate outbound call to this lead's contact.

    Idempotency and the `activities` audit row are handled by
    `dispatch_agent_tool` (this is a WRITE tool), exactly as for every other
    write tool in the registry.
    """
    try:
        check_permission(context, Permission.LEAD_ASSIGN)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        result = await make_instant_call(
            tenant_id,
            customer_id,
            lead_id,
            phone_number,
            {"reason": reason, "conversation_id": conversation_id, "source": "ai_agent"},
            session=session,
        )
        if not result.get("success", True):
            return _error_response(
                str(result.get("error_code") or "CALL_NOT_STARTED"),
                str(result.get("message") or "The call could not be started."),
            )
        return _success_response(
            {"operation": "make_instant_call", "call_job_id": result.get("call_job_id")}
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in make_instant_call_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to place the call.")


async def schedule_call_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    customer_id: UUID,
    scheduled_at: datetime | None = None,
    phone_number: str | None = None,
    reason: str | None = None,
    conversation_id: UUID | None = None,
) -> dict[str, Any]:
    """AI Tool: queue an outbound call for later. Places no call itself."""
    try:
        check_permission(context, Permission.LEAD_ASSIGN)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        job = await schedule_call(
            tenant_id,
            customer_id,
            lead_id,
            phone_number,
            scheduled_at,
            {"reason": reason, "conversation_id": conversation_id, "source": "ai_agent"},
            session=session,
        )
        return _success_response(
            {
                "operation": "schedule_call",
                "call_job_id": str(job["id"]),
                "status": str(job.get("status")),
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in schedule_call_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to schedule the call.")
