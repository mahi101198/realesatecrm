"""Background call-job scheduler.

Drives `public.call_jobs` on a timer instead of waiting for someone to poke
the `/agent/calls/*` HTTP endpoints. Each tick:

  1. Acquires a short-TTL Redis lock (SET NX PX) so exactly one process
     instance dispatches in a multi-worker deployment. If Redis is
     unreachable the tick is SKIPPED, never run unlocked -- an unlocked
     dispatcher can double-dial real customers.
  2. Skips entirely when outside the allowed calling window, so jobs are not
     transitioned queued -> ready only to be blocked at start_call and left
     stranded in `ready`.
  3. Claims due jobs via CallOrchestrator.get_next_eligible_jobs and dispatches
     each through `app.agent.tools.call_tools.make_instant_call` -- the single
     code path in this codebase that actually places a call. The worker does
     NOT talk to AgentGateway directly any more: a scheduled call and an
     instant call are literally the same call-placement code from the moment
     the job is dispatched.
  4. Periodically reconciles stuck jobs.

Every job is dispatched on its own session and inside its own try/except, so
one bad job can neither abort the batch nor kill the loop (spec: each service
fails independently; an AI/voice failure must never corrupt CRM state).

Uses stdlib `logging`, which `app.core.logging` configures with the
RedactingJsonFormatter -- structured JSON output with sensitive-key redaction.
Logging through structlog directly would bypass that redaction.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from app.agent.gateway import AgentGateway
from app.agent.orchestrator import CallOrchestrator
from app.agent.tools.call_tools import make_instant_call
from app.db.redis import get_redis_client
from app.db.session import get_db_session

logger = logging.getLogger(__name__)

# Reuse the FastAPI request-scoped session dependency verbatim as a context
# manager -- same factory, same rollback/close semantics, no second pattern.
_session_scope = asynccontextmanager(get_db_session)

# Cluster-wide lock key. One dispatcher across all app instances.
CALL_SCHEDULER_LOCK_KEY = "workers:call_scheduler:lock"

DEFAULT_INTERVAL_SECONDS = 15
DEFAULT_BATCH_SIZE = 5
# Reconcile every 20 ticks (~5 min at the default interval).
DEFAULT_RECONCILE_EVERY_TICKS = 20
DEFAULT_RECONCILE_TIMEOUT_MINUTES = 15

# Compare-and-delete: only ever release a lock we still own, so a tick that
# overran its TTL cannot delete the next holder's lock.
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


async def _acquire_lock(lock_ttl_ms: int) -> str | None:
    """Try to take the dispatcher lock. Returns the fencing token, or None if
    another instance holds it (or Redis is unavailable)."""
    redis_client = get_redis_client()
    if redis_client is None:
        logger.warning("call_scheduler: no Redis client available, skipping tick")
        return None

    token = uuid4().hex
    try:
        acquired = await redis_client.set(CALL_SCHEDULER_LOCK_KEY, token, nx=True, px=lock_ttl_ms)
    except Exception:
        logger.exception("call_scheduler: Redis lock acquisition failed, skipping tick")
        return None

    return token if acquired else None


async def _release_lock(token: str) -> None:
    """Best-effort lock release. A failure here is harmless -- the TTL expires."""
    redis_client = get_redis_client()
    if redis_client is None:
        return
    try:
        await redis_client.eval(_RELEASE_LOCK_LUA, 1, CALL_SCHEDULER_LOCK_KEY, token)
    except Exception:
        logger.warning("call_scheduler: lock release failed; lock will expire via TTL")


async def _dispatch_job(job: dict[str, Any]) -> None:
    """Dispatch one already-claimed job on its own session.

    `call_job_id` is passed explicitly, so make_instant_call dispatches THIS
    job instead of creating a second one -- the queue already persisted it.
    Raises nothing the caller must handle beyond logging.
    """
    tenant_id = job["tenant_id"]
    call_job_id = job["id"]

    async with _session_scope() as session:
        try:
            result = await make_instant_call(
                tenant_id,
                job["customer_id"],
                job["lead_id"],
                # The authoritative number is read off the tenant-scoped
                # customers row inside the gateway; nothing to pass here.
                None,
                {"source": "call_scheduler", "reason": str(job.get("job_type") or "")},
                session=session,
                call_job_id=call_job_id,
            )
        except Exception:
            # prepare_call/start_call record their own failure state
            # (retry_pending / cancelled) on the session before raising.
            # Persist that state rather than rolling the job back to queued.
            try:
                await session.commit()
            except Exception:
                await session.rollback()
            raise

        await session.commit()

    if result.get("success"):
        logger.info(f"call_scheduler: dispatched call job {call_job_id} for tenant {tenant_id}")
    else:
        logger.info(
            f"call_scheduler: call job {call_job_id} not started "
            f"(error_code={result.get('error_code')})"
        )


async def _dispatch_due_jobs(batch_size: int) -> None:
    """Claim and dispatch one batch of eligible jobs."""
    async with _session_scope() as session:
        orchestrator = CallOrchestrator(session)
        if not orchestrator.is_within_calling_window():
            logger.debug("call_scheduler: outside calling window, no dispatch this tick")
            return
        # tenant_id=None -> all tenants; this is a platform-level dispatcher.
        jobs = await orchestrator.get_next_eligible_jobs(tenant_id=None, limit=batch_size)

    if not jobs:
        return

    logger.info(f"call_scheduler: claimed {len(jobs)} eligible call job(s)")
    for job in jobs:
        try:
            await _dispatch_job(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Per-job isolation: log and continue with the rest of the batch.
            logger.exception(
                f"call_scheduler: failed to dispatch call job {job.get('id')} "
                f"for tenant {job.get('tenant_id')}"
            )


async def _reconcile_stuck_jobs(timeout_minutes: int) -> None:
    """Transition orphaned active jobs that blew past the timeout."""
    async with _session_scope() as session:
        gateway = AgentGateway(session)
        reconciled = await gateway.reconcile_stuck_jobs(
            tenant_id=None, timeout_minutes=timeout_minutes
        )
        await session.commit()

    if reconciled:
        logger.info(f"call_scheduler: reconciled {len(reconciled)} stuck call job(s)")


async def run_call_scheduler_loop(
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    reconcile_every_ticks: int = DEFAULT_RECONCILE_EVERY_TICKS,
    reconcile_timeout_minutes: int = DEFAULT_RECONCILE_TIMEOUT_MINUTES,
) -> None:
    """Poll `call_jobs` forever, dispatching due jobs under a Redis lock.

    Runs until cancelled. Cancellation propagates so the caller can await a
    clean shutdown; every other exception is logged and the loop continues.
    """
    # TTL comfortably outlasts one tick's work but still self-heals if the
    # holding process dies mid-tick.
    lock_ttl_ms = max(interval_seconds * 3, 30) * 1000
    logger.info(
        f"call_scheduler: starting loop (interval={interval_seconds}s, batch={batch_size})"
    )

    tick = 0
    try:
        while True:
            tick += 1
            token = await _acquire_lock(lock_ttl_ms)
            if token is not None:
                try:
                    await _dispatch_due_jobs(batch_size)
                    if tick % reconcile_every_ticks == 0:
                        await _reconcile_stuck_jobs(reconcile_timeout_minutes)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Never let a tick failure kill the loop.
                    logger.exception("call_scheduler: tick failed")
                finally:
                    await _release_lock(token)

            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("call_scheduler: loop cancelled, shutting down")
        raise
