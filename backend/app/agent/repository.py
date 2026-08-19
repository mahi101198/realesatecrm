"""Agent & Voice Intelligence Repository for PostgreSQL database operations."""

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import (
    AgentCustomerSummary,
    AgentLeadRequirementSummary,
    AgentPreCallContext,
    AgentPropertyInterestSummary,
    AgentRelationshipSummary,
    AgentSalesContextSummary,
)
from app.core.exceptions import NotFoundError

logger = logging.getLogger(__name__)

# Columns updatable via update_lead_requirements_with_history — must stay an
# explicit allowlist since field names are interpolated into the UPDATE SQL.
_LEAD_REQUIREMENT_FIELDS = frozenset(
    {
        "budget_min",
        "budget_max",
        "area_min",
        "area_max",
        "bedrooms",
        "purpose",
        "timeline",
        "finance_requirement",
        "preferred_city",
        "preferred_locality",
    }
)


class AgentRepository:
    """Repository handling queries for agent contexts, observations, call jobs, and CRM intelligence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def verify_lead_and_customer_tenant(
        self, tenant_id: UUID, lead_id: UUID, customer_id: UUID | None = None
    ) -> None:
        """Verify that lead (and optional customer) belong to tenant_id, raising NotFoundError if not."""
        sql = text(
            """
            SELECT l.id
            FROM public.leads l
            JOIN public.customers c ON l.customer_id = c.id
            WHERE l.id = :lead_id
              AND l.tenant_id = :tenant_id
              AND l.deleted_at IS NULL
              AND (:customer_id::uuid IS NULL OR c.id = :customer_id)
            """
        )
        res = await self.session.execute(
            sql, {"lead_id": lead_id, "tenant_id": tenant_id, "customer_id": customer_id}
        )
        if not res.mappings().one_or_none():
            raise NotFoundError(
                message="Resource was not found for this tenant.",
                code="NOT_FOUND",
            )


    # ---------------------------------------------------------------------------
    # PRE-CALL CONTEXT BUILDER (DETERMINISTIC)
    # ---------------------------------------------------------------------------

    async def get_pre_call_context(
        self, tenant_id: UUID | None, lead_id: UUID
    ) -> AgentPreCallContext | None:
        """Fetch and aggregate structured pre-call context snapshot for AI agent."""
        # 1. Lead + Customer + Requirement
        lead_query = text(
            """
            SELECT
                l.id AS lead_id,
                l.tenant_id,
                l.lead_number,
                l.status,
                l.sales_stage,
                l.lead_score,
                l.budget_min,
                l.budget_max,
                l.area_min,
                l.area_max,
                l.bedrooms,
                l.purpose,
                l.timeline,
                l.finance_requirement,
                l.preferred_city,
                l.preferred_locality,
                pt.name AS property_type_name,
                c.id AS customer_id,
                c.full_name AS customer_name,
                c.phone AS customer_phone,
                c.alternate_phone AS customer_alt_phone,
                c.email AS customer_email,
                c.city AS customer_city,
                c.preferred_language AS customer_lang,
                c.do_not_call AS customer_dnc,
                sa.id AS sales_agent_id,
                u.name AS sales_agent_name,
                u.phone AS sales_agent_phone
            FROM public.leads l
            JOIN public.customers c ON l.customer_id = c.id
            LEFT JOIN public.property_types pt ON l.property_type_id = pt.id
            LEFT JOIN public.sales_agents sa ON l.assigned_sales_agent_id = sa.id
            LEFT JOIN public.users u ON sa.user_id = u.id
            WHERE l.id = :lead_id
              AND l.deleted_at IS NULL
              AND (:tenant_id::uuid IS NULL OR l.tenant_id = :tenant_id)
            """
        )
        res = await self.session.execute(lead_query, {"lead_id": lead_id, "tenant_id": tenant_id})
        row = res.mappings().one_or_none()
        if not row:
            return None

        # Determine temperature
        score = row["lead_score"] or 0
        if score >= 80:
            temperature = "hot"
        elif score >= 40:
            temperature = "warm"
        else:
            temperature = "cold"

        # 2. Property interests
        interests_query = text(
            """
            SELECT
                lpi.project_id,
                p.name AS project_name,
                lpi.property_id,
                lpi.interest_level,
                lpi.is_primary
            FROM public.lead_property_interests lpi
            LEFT JOIN public.projects p ON lpi.project_id = p.id
            WHERE lpi.lead_id = :lead_id
            ORDER BY lpi.is_primary DESC, lpi.created_at DESC
            LIMIT 5
            """
        )
        interests_res = await self.session.execute(interests_query, {"lead_id": lead_id})
        interests_rows = interests_res.mappings().all()

        # 3. Call relationship history
        call_hist_query = text(
            """
            SELECT
                COUNT(id) AS previous_calls,
                MAX(created_at) AS last_call_at
            FROM public.calls
            WHERE lead_id = :lead_id
            """
        )
        call_hist_res = await self.session.execute(call_hist_query, {"lead_id": lead_id})
        call_hist_row = call_hist_res.mappings().one()

        # Last call outcome
        last_outcome_query = text(
            """
            SELECT outcome FROM public.calls
            WHERE lead_id = :lead_id AND outcome IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        last_outcome_res = await self.session.execute(last_outcome_query, {"lead_id": lead_id})
        last_outcome_row = last_outcome_res.mappings().one_or_none()
        last_outcome = last_outcome_row["outcome"] if last_outcome_row else None

        # Check open follow-ups
        followup_query = text(
            """
            SELECT scheduled_at FROM public.lead_follow_ups
            WHERE lead_id = :lead_id AND status IN ('scheduled', 'due', 'queued', 'pending')
            ORDER BY scheduled_at ASC
            LIMIT 1
            """
        )
        followup_res = await self.session.execute(followup_query, {"lead_id": lead_id})
        followup_row = followup_res.mappings().one_or_none()
        open_follow_up = followup_row is not None
        open_follow_up_at = followup_row["scheduled_at"] if followup_row else None

        # 4. Lead Observations (objections, decision maker, competitors)
        obs_query = text(
            """
            SELECT observation_type, observation_value
            FROM public.lead_observations
            WHERE lead_id = :lead_id
            ORDER BY created_at DESC
            LIMIT 20
            """
        )
        obs_res = await self.session.execute(obs_query, {"lead_id": lead_id})
        obs_rows = obs_res.mappings().all()

        main_objections = []
        observations_list = []
        competitors = []
        decision_maker = None

        for r in obs_rows:
            obs_type = r["observation_type"]
            val = r["observation_value"]
            if obs_type == "objection":
                main_objections.append(val)
            elif obs_type == "competitor_mentioned":
                competitors.append(val)
            elif obs_type == "decision_maker":
                decision_maker = val
            else:
                observations_list.append(f"{obs_type}: {val}")

        # 5. Recent notes
        notes_query = text(
            """
            SELECT note_text FROM public.lead_notes
            WHERE lead_id = :lead_id
            ORDER BY created_at DESC
            LIMIT 5
            """
        )
        notes_res = await self.session.execute(notes_query, {"lead_id": lead_id})
        recent_notes = [r["note_text"] for r in notes_res.mappings().all()]

        # Preferred location string
        loc_parts = [p for p in [row["preferred_locality"], row["preferred_city"]] if p]
        pref_location = ", ".join(loc_parts) if loc_parts else None

        return AgentPreCallContext(
            lead_id=row["lead_id"],
            tenant_id=row["tenant_id"],
            lead_number=row["lead_number"],
            status=row["status"],
            sales_stage=row["sales_stage"],
            lead_score=score,
            temperature=temperature,
            customer=AgentCustomerSummary(
                customer_id=row["customer_id"],
                full_name=row["customer_name"],
                phone=row["customer_phone"],
                alternate_phone=row["customer_alt_phone"],
                email=row["customer_email"],
                city=row["customer_city"],
                preferred_language=row["customer_lang"] or "hi",
            ),
            requirement=AgentLeadRequirementSummary(
                property_type=row["property_type_name"],
                preferred_location=pref_location,
                budget_min=row["budget_min"],
                budget_max=row["budget_max"],
                area_min=row["area_min"],
                area_max=row["area_max"],
                bedrooms=row["bedrooms"],
                purpose=str(row["purpose"]) if row["purpose"] else None,
                timeline=row["timeline"],
                financing_requirement=str(row["finance_requirement"])
                if row["finance_requirement"]
                else None,
            ),
            property_interests=[
                AgentPropertyInterestSummary(
                    project_id=r["project_id"],
                    project_name=r["project_name"],
                    property_id=r["property_id"],
                    interest_level=r["interest_level"],
                    is_primary=r["is_primary"],
                )
                for r in interests_rows
            ],
            relationship=AgentRelationshipSummary(
                previous_calls=call_hist_row["previous_calls"] or 0,
                last_outcome=str(last_outcome) if last_outcome else None,
                last_call_at=call_hist_row["last_call_at"],
                open_follow_up=open_follow_up,
                open_follow_up_at=open_follow_up_at,
            ),
            sales_context=AgentSalesContextSummary(
                main_objections=main_objections,
                observations=observations_list,
                decision_maker=decision_maker,
                competitors_mentioned=competitors,
            ),
            recent_notes=recent_notes,
        )

    # ---------------------------------------------------------------------------
    # CONCURRENCY-SAFE JOB CLAIMING (SELECT FOR UPDATE SKIP LOCKED)
    # ---------------------------------------------------------------------------

    async def claim_job_for_update(
        self, tenant_id: UUID, call_job_id: UUID
    ) -> dict[str, Any] | None:
        """Lock call job row exclusively using FOR UPDATE SKIP LOCKED to prevent double worker claiming."""
        sql = text(
            """
            SELECT * FROM public.call_jobs
            WHERE id = :call_job_id AND tenant_id = :tenant_id
            FOR UPDATE SKIP LOCKED
            """
        )
        res = await self.session.execute(sql, {"call_job_id": call_job_id, "tenant_id": tenant_id})
        row = res.mappings().one_or_none()
        return dict(row) if row else None

    # ---------------------------------------------------------------------------
    # IDEMPOTENCY
    # ---------------------------------------------------------------------------

    async def get_idempotent_response(
        self, tenant_id: UUID, idempotency_key: str, operation_type: str
    ) -> dict[str, Any] | None:
        """Check if an operation was already executed using idempotency key."""
        sql = text(
            """
            SELECT response_payload FROM public.operation_idempotency_keys
            WHERE tenant_id = :tenant_id
              AND idempotency_key = :idempotency_key
              AND operation_type = :operation_type
            """
        )
        res = await self.session.execute(
            sql,
            {
                "tenant_id": tenant_id,
                "idempotency_key": idempotency_key,
                "operation_type": operation_type,
            },
        )
        row = res.mappings().one_or_none()
        return row["response_payload"] if row else None

    async def save_idempotent_response(
        self,
        tenant_id: UUID,
        idempotency_key: str,
        operation_type: str,
        response_payload: dict[str, Any],
    ) -> None:
        """Save executed operation result under idempotency key."""
        sql = text(
            """
            INSERT INTO public.operation_idempotency_keys (
                tenant_id, idempotency_key, operation_type, response_payload
            ) VALUES (
                :tenant_id, :idempotency_key, :operation_type, :response_payload
            )
            ON CONFLICT (tenant_id, idempotency_key, operation_type) DO NOTHING
            """
        )
        await self.session.execute(
            sql,
            {
                "tenant_id": tenant_id,
                "idempotency_key": idempotency_key,
                "operation_type": operation_type,
                "response_payload": json.dumps(response_payload),
            },
        )

    # ---------------------------------------------------------------------------
    # SNAPSHOT PERSISTENCE
    # ---------------------------------------------------------------------------

    async def save_agent_call_context_snapshot(
        self,
        tenant_id: UUID,
        call_job_id: UUID,
        lead_id: UUID,
        customer_id: UUID,
        context_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Save historical pre-call context snapshot into agent_call_contexts."""
        sql = text(
            """
            INSERT INTO public.agent_call_contexts (
                tenant_id, call_job_id, lead_id, customer_id, context_payload, context_version
            ) VALUES (
                :tenant_id, :call_job_id, :lead_id, :customer_id, :context_payload, 1
            )
            RETURNING *
            """
        )
        res = await self.session.execute(
            sql,
            {
                "tenant_id": tenant_id,
                "call_job_id": call_job_id,
                "lead_id": lead_id,
                "customer_id": customer_id,
                "context_payload": json.dumps(context_payload),
            },
        )
        return dict(res.mappings().one())

    # ---------------------------------------------------------------------------
    # STUCK JOB RECOVERY
    # ---------------------------------------------------------------------------

    async def find_and_reconcile_stuck_jobs(
        self, tenant_id: UUID | None = None, timeout_minutes: int = 15
    ) -> list[dict[str, Any]]:
        """Find jobs stuck in 'preparing', 'ready', or 'calling' beyond timeout and transition to retry_pending/failed."""
        stuck_query = text(
            """
            SELECT id, tenant_id, lead_id, customer_id, status, attempt_count, max_attempts
            FROM public.call_jobs
            WHERE status IN ('preparing', 'ready', 'calling')
              AND updated_at < (NOW() - INTERVAL '1 minute' * :timeout_minutes)
              AND (:tenant_id::uuid IS NULL OR tenant_id = :tenant_id)
            FOR UPDATE SKIP LOCKED
            """
        )
        res = await self.session.execute(
            stuck_query, {"tenant_id": tenant_id, "timeout_minutes": timeout_minutes}
        )
        stuck_rows = [dict(r) for r in res.mappings().all()]

        reconciled = []
        for job in stuck_rows:
            job_id = job["id"]
            attempts = job["attempt_count"]
            max_att = job["max_attempts"]

            new_status = "retry_pending" if attempts < max_att else "failed"

            upd_sql = text(
                """
                UPDATE public.call_jobs
                SET status = :new_status::public.call_job_status,
                    last_error_code = 'STUCK_JOB_TIMEOUT',
                    last_error_message = 'Job timed out in active state and was reconciled.',
                    updated_at = NOW()
                WHERE id = :job_id
                """
            )
            await self.session.execute(upd_sql, {"new_status": new_status, "job_id": job_id})
            reconciled.append({"job_id": str(job_id), "new_status": new_status})

        return reconciled

    # ---------------------------------------------------------------------------
    # REQUIREMENTS UPDATES WITH HISTORY AUDIT
    # ---------------------------------------------------------------------------

    async def update_lead_requirements_with_history(
        self,
        tenant_id: UUID,
        lead_id: UUID,
        updates: dict[str, Any],
        source_call_attempt_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Update structured requirements on lead and log exact diffs in lead_requirement_history."""
        fetch_query = text(
            """
            SELECT budget_min, budget_max, area_min, area_max, bedrooms, purpose, timeline, finance_requirement, preferred_city, preferred_locality
            FROM public.leads
            WHERE id = :lead_id AND tenant_id = :tenant_id
            """
        )
        res = await self.session.execute(fetch_query, {"lead_id": lead_id, "tenant_id": tenant_id})
        current_row = res.mappings().one_or_none()
        if not current_row:
            raise ValueError(f"Lead {lead_id} not found.")

        history_records = []
        set_clauses = []
        params: dict[str, Any] = {"lead_id": lead_id, "tenant_id": tenant_id}

        for field, new_val in updates.items():
            if new_val is None:
                continue
            if field not in _LEAD_REQUIREMENT_FIELDS:
                raise ValueError(f"Field '{field}' is not an updatable lead requirement.")
            old_val = current_row.get(field)
            if str(old_val) != str(new_val):
                set_clauses.append(f"{field} = :{field}")
                params[field] = new_val
                history_records.append(
                    {
                        "tenant_id": tenant_id,
                        "lead_id": lead_id,
                        "field_name": field,
                        "old_value": str(old_val) if old_val is not None else None,
                        "new_value": str(new_val),
                        "source": "ai_agent",
                        "source_call_attempt_id": source_call_attempt_id,
                    }
                )

        if set_clauses:
            update_sql = text(
                f"""
                UPDATE public.leads
                SET {", ".join(set_clauses)}, updated_at = NOW()
                WHERE id = :lead_id AND tenant_id = :tenant_id
                RETURNING *
                """  # noqa: S608
            )
            upd_res = await self.session.execute(update_sql, params)
            updated_lead = dict(upd_res.mappings().one())

            for hist in history_records:
                hist_sql = text(
                    """
                    INSERT INTO public.lead_requirement_history (
                        tenant_id, lead_id, field_name, old_value, new_value, source, source_call_attempt_id
                    ) VALUES (
                        :tenant_id, :lead_id, :field_name, :old_value, :new_value, :source, :source_call_attempt_id
                    )
                    """
                )
                await self.session.execute(hist_sql, hist)

            return updated_lead

        return dict(current_row)

    # ---------------------------------------------------------------------------
    # OBSERVATIONS
    # ---------------------------------------------------------------------------

    async def record_observation(
        self,
        tenant_id: UUID,
        lead_id: UUID,
        customer_id: UUID,
        observation_type: str,
        observation_value: str,
        confidence: float = 1.0,
        source_call_attempt_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Record a conversational intelligence observation for a lead."""
        await self.verify_lead_and_customer_tenant(tenant_id, lead_id, customer_id)
        sql = text(
            """
            INSERT INTO public.lead_observations (
                tenant_id, lead_id, customer_id, observation_type, observation_value, confidence, source, source_call_attempt_id
            ) VALUES (
                :tenant_id, :lead_id, :customer_id, :observation_type, :observation_value, :confidence, 'ai_call', :source_call_attempt_id
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
                "observation_type": observation_type,
                "observation_value": observation_value,
                "confidence": confidence,
                "source_call_attempt_id": source_call_attempt_id,
            },
        )
        return dict(res.mappings().one())

    # ---------------------------------------------------------------------------
    # LEAD FOLLOW-UPS
    # ---------------------------------------------------------------------------

    async def create_lead_follow_up(
        self,
        tenant_id: UUID,
        lead_id: UUID,
        customer_id: UUID,
        scheduled_at: datetime,
        follow_up_type: str = "ai_call",
        reason: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Create a future follow-up obligation for a lead."""
        await self.verify_lead_and_customer_tenant(tenant_id, lead_id, customer_id)
        dup_check = text(
            """
            SELECT id FROM public.lead_follow_ups
            WHERE lead_id = :lead_id
              AND status IN ('scheduled', 'due', 'queued', 'pending')
              AND ABS(EXTRACT(EPOCH FROM (scheduled_at - :scheduled_at))) < 900
            LIMIT 1
            """
        )
        dup_res = await self.session.execute(
            dup_check, {"lead_id": lead_id, "scheduled_at": scheduled_at}
        )
        existing = dup_res.mappings().one_or_none()
        if existing:
            upd_sql = text(
                """
                UPDATE public.lead_follow_ups
                SET scheduled_at = :scheduled_at, reason = COALESCE(:reason, reason), notes = COALESCE(:notes, notes), updated_at = NOW()
                WHERE id = :id
                RETURNING *
                """
            )
            upd_res = await self.session.execute(
                upd_sql,
                {
                    "id": existing["id"],
                    "scheduled_at": scheduled_at,
                    "reason": reason,
                    "notes": notes,
                },
            )
            return dict(upd_res.mappings().one())

        sql = text(
            """
            INSERT INTO public.lead_follow_ups (
                tenant_id, lead_id, customer_id, follow_up_type, status, reason, scheduled_at, notes, source
            ) VALUES (
                :tenant_id, :lead_id, :customer_id, :follow_up_type, 'scheduled', :reason, :scheduled_at, :notes, 'ai_agent'
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
                "follow_up_type": follow_up_type,
                "reason": reason,
                "scheduled_at": scheduled_at,
                "notes": notes,
            },
        )
        row = dict(res.mappings().one())

        upd_lead = text(
            """
            UPDATE public.leads
            SET next_follow_up_at = :scheduled_at, updated_at = NOW()
            WHERE id = :lead_id AND tenant_id = :tenant_id
            """
        )
        await self.session.execute(
            upd_lead, {"lead_id": lead_id, "tenant_id": tenant_id, "scheduled_at": scheduled_at}
        )

        return row

    async def get_open_follow_ups(self, tenant_id: UUID, lead_id: UUID) -> list[dict[str, Any]]:
        """Get pending/scheduled open follow-ups for a lead."""
        sql = text(
            """
            SELECT * FROM public.lead_follow_ups
            WHERE tenant_id = :tenant_id AND lead_id = :lead_id
              AND status IN ('scheduled', 'due', 'queued', 'pending')
            ORDER BY scheduled_at ASC
            """
        )
        res = await self.session.execute(sql, {"tenant_id": tenant_id, "lead_id": lead_id})
        return [dict(r) for r in res.mappings().all()]

    async def reschedule_follow_up(
        self, tenant_id: UUID, follow_up_id: UUID, scheduled_at: datetime, reason: str | None = None
    ) -> dict[str, Any]:
        """Reschedule an existing open follow-up."""
        sql = text(
            """
            UPDATE public.lead_follow_ups
            SET scheduled_at = :scheduled_at, reason = COALESCE(:reason, reason), status = 'scheduled', updated_at = NOW()
            WHERE id = :follow_up_id AND tenant_id = :tenant_id
            RETURNING *
            """
        )
        res = await self.session.execute(
            sql,
            {
                "follow_up_id": follow_up_id,
                "tenant_id": tenant_id,
                "scheduled_at": scheduled_at,
                "reason": reason,
            },
        )
        row = res.mappings().one_or_none()
        if not row:
            raise NotFoundError(message=f"Follow-up {follow_up_id} not found.", code="NOT_FOUND")
        return dict(row)

    async def cancel_follow_up(
        self, tenant_id: UUID, follow_up_id: UUID, cancel_reason: str | None = None
    ) -> dict[str, Any]:
        """Cancel an open follow-up and any associated queued call jobs."""
        sql = text(
            """
            UPDATE public.lead_follow_ups
            SET status = 'cancelled', cancelled_at = NOW(), cancel_reason = :cancel_reason, updated_at = NOW()
            WHERE id = :follow_up_id AND tenant_id = :tenant_id
            RETURNING *
            """
        )
        res = await self.session.execute(
            sql,
            {
                "follow_up_id": follow_up_id,
                "tenant_id": tenant_id,
                "cancel_reason": cancel_reason,
            },
        )
        row = res.mappings().one_or_none()
        if not row:
            raise NotFoundError(message=f"Follow-up {follow_up_id} not found.", code="NOT_FOUND")

        # Cancel pending call jobs for this lead
        cancel_jobs_sql = text(
            """
            UPDATE public.call_jobs
            SET status = 'cancelled', last_error_code = 'FOLLOW_UP_CANCELLED', last_error_message = 'Associated follow-up was cancelled.', updated_at = NOW()
            WHERE lead_id = :lead_id AND tenant_id = :tenant_id AND status IN ('queued', 'scheduled', 'preparing', 'ready', 'retry_pending')
            """
        )
        await self.session.execute(cancel_jobs_sql, {"lead_id": row["lead_id"], "tenant_id": tenant_id})

        return dict(row)

    # ---------------------------------------------------------------------------
    # SALES HANDOFFS
    # ---------------------------------------------------------------------------

    async def create_sales_handoff(
        self,
        tenant_id: UUID,
        lead_id: UUID,
        customer_id: UUID,
        reason: str | None = None,
        priority: int = 5,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Request a human sales agent transfer/handoff."""
        await self.verify_lead_and_customer_tenant(tenant_id, lead_id, customer_id)
        sql = text(
            """
            INSERT INTO public.sales_handoffs (
                tenant_id, lead_id, customer_id, reason, priority, status, notes
            ) VALUES (
                :tenant_id, :lead_id, :customer_id, :reason, :priority, 'requested', :notes
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
                "reason": reason,
                "priority": priority,
                "notes": notes,
            },
        )
        return dict(res.mappings().one())

    # ---------------------------------------------------------------------------
    # DO-NOT-CALL & CONTACT PREFERENCES
    # ---------------------------------------------------------------------------

    async def check_do_not_call(self, tenant_id: UUID, customer_id: UUID) -> bool:
        """Check if customer is marked Do-Not-Call in customers or lead_contact_preferences."""
        sql = text(
            """
            SELECT c.do_not_call AS cust_dnc, lcp.do_not_call AS pref_dnc
            FROM public.customers c
            LEFT JOIN public.lead_contact_preferences lcp ON c.id = lcp.customer_id
            WHERE c.id = :customer_id AND c.tenant_id = :tenant_id
            """
        )
        res = await self.session.execute(sql, {"customer_id": customer_id, "tenant_id": tenant_id})
        row = res.mappings().one_or_none()
        if not row:
            return True
        return bool(row["cust_dnc"] or row["pref_dnc"])

    async def get_sales_handoff(
        self, tenant_id: UUID, handoff_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch a sales handoff by ID, scoped to tenant."""
        sql = text(
            """
            SELECT sh.*, c.phone AS customer_phone
            FROM public.sales_handoffs sh
            JOIN public.customers c ON c.id = sh.customer_id
            WHERE sh.id = :id AND sh.tenant_id = :tenant_id
            """
        )
        res = await self.session.execute(sql, {"id": handoff_id, "tenant_id": tenant_id})
        row = res.mappings().one_or_none()
        return dict(row) if row else None

    async def get_user_phone(self, tenant_id: UUID, user_id: UUID) -> str | None:
        """Fetch a staff user's registered phone number, scoped to tenant."""
        sql = text(
            """
            SELECT phone FROM public.users
            WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
            """
        )
        res = await self.session.execute(sql, {"id": user_id, "tenant_id": tenant_id})
        row = res.mappings().one_or_none()
        return row["phone"] if row else None

    async def accept_sales_handoff(
        self, tenant_id: UUID, handoff_id: UUID, assigned_user_id: UUID
    ) -> dict[str, Any] | None:
        """Mark a handoff accepted by a specific staff member. Only succeeds
        from status IN ('requested', 'queued') -- returns None otherwise,
        so a double-accept race is a clean no-op, not a silent overwrite."""
        sql = text(
            """
            UPDATE public.sales_handoffs
            SET status = 'accepted',
                assigned_user_id = :assigned_user_id,
                accepted_at = NOW()
            WHERE id = :id AND tenant_id = :tenant_id
              AND status IN ('requested', 'queued')
            RETURNING *
            """
        )
        res = await self.session.execute(
            sql,
            {"id": handoff_id, "tenant_id": tenant_id, "assigned_user_id": assigned_user_id},
        )
        row = res.mappings().one_or_none()
        return dict(row) if row else None
