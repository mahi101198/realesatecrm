"""Application-wide constants."""

API_V1_PREFIX: str = "/api/v1"

# Request & Security Headers
REQUEST_ID_HEADER: str = "X-Request-ID"
RESPONSE_TIME_HEADER: str = "X-Response-Time"

# Pagination defaults
DEFAULT_PAGE_SIZE: int = 25
MAX_PAGE_SIZE: int = 100

# Health check parameters
HEALTH_CHECK_TIMEOUT_SECONDS: float = 5.0

# Role precedence for multi-role users (highest first).
# Used only to pick RequestContext.role for display; permissions are always unioned.
ROLE_PRECEDENCE: tuple[str, ...] = (
    "super_admin",
    "admin",
    "manager",
    "sales_manager",
    "sales_agent",
    "viewer",
)

# Application user status that is allowed to access the API.
ACTIVE_USER_STATUS: str = "active"

# Redacted keys for logging safety
SENSITIVE_KEYS: set[str] = {
    "authorization",
    "bearer",
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "service_role_key",
    "supabase_service_role_key",
    "jwt_secret",
    "cookie",
}
