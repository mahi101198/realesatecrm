"""The AI layer's synthetic, READ-ONLY, tenant-scoped RequestContext.

WHY THIS EXISTS
---------------
The registered tools in `app/agent/tools/read_tools.py` take a
`RequestContext` because they were written for authenticated HTTP requests.
The orchestrator has no authenticated user: it is triggered by an inbound
Meta webhook, where the only trusted identity is the tenant taken from the
webhook URL path (already verified by the webhook signature check before the
service layer is reached).

So the AI layer builds a context that is:

  * SCOPED    -- `scope = TENANT` with the tenant id from the webhook, so
                 `resolve_tenant_scope` returns exactly that tenant and
                 `ensure_tenant_resource_access` rejects anything else. There
                 is no path here that yields GLOBAL scope or a None tenant.
  * READ-ONLY -- `permissions` is a frozen allowlist of exactly four read
                 codes. `check_permission` on any write tool (LEAD_UPDATE,
                 APPOINTMENT_CREATE, LEAD_ASSIGN, ...) raises ForbiddenError,
                 so this context CANNOT be used to mutate anything even if a
                 future caller passes it to the wrong tool.
  * NOT A USER -- `is_super_admin` is False and `user_id` is the all-zero
                 sentinel. Nothing writes it to a FK column: the read tools
                 never touch `context.user_id`, and every WRITE the
                 orchestrator performs goes through a core service function
                 that takes an explicit `tenant_id` and a nullable actor,
                 never through a `*_tool` registry wrapper.

DO NOT widen `AI_READ_PERMISSIONS`. If the AI needs to write, route it
through the orchestrator's executor nodes, which call the underlying
services directly with an explicit tenant id -- that is the audited path.
"""

from uuid import UUID

from app.core.permissions import Permission
from app.core.request_context import RequestContext, SecurityScope

# Sentinel actor id for AI-originated activity. Deliberately the nil UUID so
# it is obviously not a real user in any log or audit row.
AI_AGENT_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000000")

# Exactly the permissions the WhatsApp agent's read-only tool surface needs.
AI_READ_PERMISSIONS = frozenset(
    {
        Permission.LEAD_READ.value,
        Permission.PROPERTY_READ.value,
        Permission.PROJECT_READ.value,
        Permission.APPOINTMENT_READ.value,
    }
)


def build_agent_read_context(tenant_id: UUID, *, request_id: str = "ai-agent") -> RequestContext:
    """Build the read-only, single-tenant context the AI layer uses to call
    registered READ tools. Raises nothing; the caller must already have a
    verified tenant id."""
    return RequestContext(
        request_id=request_id,
        user_id=AI_AGENT_ACTOR_ID,
        auth_user_id=AI_AGENT_ACTOR_ID,
        tenant_id=tenant_id,
        role="ai_agent",
        permissions=AI_READ_PERMISSIONS,
        is_super_admin=False,
        scope=SecurityScope.TENANT,
        metadata={"actor": "ai_agent", "access": "read_only"},
    )
