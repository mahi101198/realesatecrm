"""Agent REST API Router."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.gateway import AgentGateway
from app.agent.handoff_service import SalesHandoffService
from app.agent.orchestrator import CallContextService, CallOrchestrator
from app.agent.schemas import (
    AgentPreCallContext,
    CallCompleteInput,
    CallFilter,
    CallJobCreateInput,
    CallPrepareInput,
    CallResponse,
    SalesHandoffFilter,
    SalesHandoffResponse,
    ToolExecuteRequest,
    ToolResponse,
)
from app.agent.tools import dispatch_agent_tool
from app.auth.dependencies import get_request_context_dep, require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/agent", tags=["AI Voice Agent CRM"])


def _tenant_required_error() -> dict[str, Any]:
    return {
        "success": False,
        "error_code": "TENANT_REQUIRED",
        "message": "Tenant scope is required.",
    }


@router.get(
    "/context/{lead_id}",
    response_model=AgentPreCallContext,
    status_code=status.HTTP_200_OK,
    summary="Get Pre-Call Context",
    description=(
        "Retrieve authoritative, deterministic pre-call context snapshot for "
        "AI Voice Agent at call start."
    ),
)
async def get_agent_pre_call_context(
    lead_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> AgentPreCallContext:
    """Get pre-call context snapshot."""
    tenant_id = resolve_tenant_scope(context)
    service = CallContextService(session)
    lead_ctx = await service.build_pre_call_context(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead_ctx.tenant_id)
    return lead_ctx


@router.post(
    "/tools/execute",
    response_model=ToolResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Agent Tool",
    description=(
        "Invoke one of the 20 registered AI Agent tools securely with "
        "authorization, tenant scope enforcement, and idempotency."
    ),
)
async def execute_agent_tool(
    body: ToolExecuteRequest,
    context: RequestContext = Depends(get_request_context_dep),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResponse:
    """Dispatch and execute tool call."""
    args = dict(body.arguments)
    if "lead_id" not in args:
        args["lead_id"] = body.lead_id

    result = await dispatch_agent_tool(
        body.tool_name, context, session, args, idempotency_key=body.idempotency_key
    )
    if result.get("success"):
        return ToolResponse(success=True, data=result.get("data"))
    return ToolResponse(
        success=False,
        error_code=result.get("error_code"),
        message=result.get("message"),
    )


@router.post(
    "/calls/prepare",
    status_code=status.HTTP_200_OK,
    summary="Prepare Call Job",
    description=(
        "Prepare call job for external AI agent: generates snapshot context "
        "and transitions state queued -> preparing -> ready."
    ),
)
async def prepare_call(
    body: CallPrepareInput,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Prepare call job."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        return _tenant_required_error()

    gateway = AgentGateway(session)
    res = await gateway.prepare_call(tenant_id, body.call_job_id)
    return {"success": True, "data": res}


@router.post(
    "/calls/start",
    status_code=status.HTTP_200_OK,
    summary="Start Call Attempt",
    description=(
        "Re-check DNC, verify calling window & concurrency, claim job, and "
        "record call attempt."
    ),
)
async def start_call(
    body: CallPrepareInput,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Start call attempt."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        return _tenant_required_error()

    gateway = AgentGateway(session)
    return await gateway.start_call(tenant_id, body.call_job_id)


@router.post(
    "/calls/complete",
    status_code=status.HTTP_200_OK,
    summary="Record Call Completion",
    description=(
        "Record call attempt completion, apply outcome retry rules, and "
        "finalize call job status."
    ),
)
async def complete_call(
    body: CallCompleteInput,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Record call completion."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        return _tenant_required_error()

    gateway = AgentGateway(session)
    res = await gateway.record_call_completed(
        tenant_id=tenant_id,
        call_job_id=body.call_job_id,
        call_attempt_id=body.call_attempt_id,
        outcome=body.outcome,
        duration_seconds=body.duration_seconds,
        call_summary=body.call_summary,
        provider_call_id=body.provider_call_id,
        termination_reason=body.termination_reason,
        failure_code=body.failure_code,
        failure_message=body.failure_message,
    )
    return {"success": True, "data": res}


@router.post(
    "/calls/reconcile-stuck",
    status_code=status.HTTP_200_OK,
    summary="Reconcile Stuck Jobs",
    description=(
        "Reconcile active call jobs stuck in preparing, ready, or calling "
        "beyond timeout threshold."
    ),
)
async def reconcile_stuck_jobs(
    timeout_minutes: int = Query(15, ge=1, le=120),
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Reconcile stuck jobs."""
    tenant_id = resolve_tenant_scope(context)
    gateway = AgentGateway(session)
    reconciled = await gateway.reconcile_stuck_jobs(
        tenant_id=tenant_id, timeout_minutes=timeout_minutes
    )
    return {"success": True, "data": reconciled, "reconciled_count": len(reconciled)}


@router.post(
    "/call-jobs",
    status_code=status.HTTP_201_CREATED,
    summary="Create Call Job",
    description="Queue a new call job for automated outbound AI dialer.",
)
async def create_call_job(
    body: CallJobCreateInput,
    customer_id: UUID = Query(..., description="Customer ID associated with the lead"),
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a call job."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        return _tenant_required_error()

    orchestrator = CallOrchestrator(session)
    job = await orchestrator.create_call_job(
        tenant_id=tenant_id,
        lead_id=body.lead_id,
        customer_id=customer_id,
        job_type=body.job_type,
        priority=body.priority,
        scheduled_at=body.scheduled_at,
    )
    return {"success": True, "data": job}


@router.post(
    "/sales-handoffs/{handoff_id}/accept",
    status_code=status.HTTP_200_OK,
    summary="Accept Sales Handoff",
    description=(
        "A staff member accepts an open sales handoff request. Places a real "
        "Superfone CRM click-to-call bridge (staff phone <-> customer phone) "
        "before marking the handoff accepted -- a failed bridge leaves the "
        "handoff open rather than falsely marking it accepted."
    ),
)
async def accept_sales_handoff(
    handoff_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.LEAD_ASSIGN)),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Accept sales handoff and place click-to-call bridge."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        return _tenant_required_error()

    service = SalesHandoffService(session)
    result = await service.accept_handoff(tenant_id, handoff_id, context.user_id)
    return {"success": True, "data": result}


@router.get(
    "/call-jobs/eligible",
    status_code=status.HTTP_200_OK,
    summary="Get Eligible Call Jobs",
    description="Retrieve next prioritized call jobs ready for dispatching.",
)
async def get_eligible_call_jobs(
    limit: int = Query(5, ge=1, le=50),
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get ready call jobs."""
    tenant_id = resolve_tenant_scope(context)
    orchestrator = CallOrchestrator(session)
    jobs = await orchestrator.get_next_eligible_jobs(tenant_id=tenant_id, limit=limit)
    return {"success": True, "data": jobs}


@router.get(
    "/calls",
    response_model=PaginatedResponse[CallResponse],
    status_code=status.HTTP_200_OK,
    summary="List Calls",
    description=(
        "List and filter voice/telephony calls with pagination, for "
        "dashboard/reporting reads."
    ),
)
async def list_calls(
    lead_id: UUID | None = Query(default=None, description="Filter by lead ID"),
    customer_id: UUID | None = Query(default=None, description="Filter by customer ID"),
    call_status: str | None = Query(
        default=None, alias="status", description="Filter by call status"
    ),
    outcome: str | None = Query(default=None, description="Filter by call outcome"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[CallResponse]:
    """List calls endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = CallFilter(
        lead_id=lead_id, customer_id=customer_id, status=call_status, outcome=outcome
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    gateway = AgentGateway(session)
    return await gateway.list_calls(tenant_id, filters, pagination)


@router.get(
    "/sales-handoffs",
    response_model=PaginatedResponse[SalesHandoffResponse],
    status_code=status.HTTP_200_OK,
    summary="List Sales Handoffs",
    description=(
        "List and filter AI-to-human sales handoff requests with pagination, "
        "for dashboard/reporting reads."
    ),
)
async def list_sales_handoffs(
    lead_id: UUID | None = Query(default=None, description="Filter by lead ID"),
    handoff_status: str | None = Query(
        default=None, alias="status", description="Filter by handoff status"
    ),
    assigned_user_id: UUID | None = Query(default=None, description="Filter by assigned user"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[SalesHandoffResponse]:
    """List sales handoffs endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = SalesHandoffFilter(
        lead_id=lead_id, status=handoff_status, assigned_user_id=assigned_user_id
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = SalesHandoffService(session)
    return await service.list_handoffs(tenant_id, filters, pagination)
