"""Unified Application Exception Hierarchy."""

from typing import Any


class AppError(Exception):
    """Base class for all domain and infrastructure application errors."""

    def __init__(
        self,
        message: str = "An unexpected error occurred.",
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class NotFoundError(AppError):
    """Raised when a requested resource is not found."""

    def __init__(
        self,
        message: str = "The requested resource was not found.",
        code: str = "NOT_FOUND",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=404,
            details=details,
        )


class UnauthorizedError(AppError):
    """Raised when authentication credentials are missing or invalid."""

    def __init__(
        self,
        message: str = "Authentication is required to access this resource.",
        code: str = "UNAUTHORIZED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=401,
            details=details,
        )


class ForbiddenError(AppError):
    """Raised when an authenticated user lacks permission for the operation."""

    def __init__(
        self,
        message: str = "You do not have permission to perform this action.",
        code: str = "FORBIDDEN",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=403,
            details=details,
        )


class ConflictError(AppError):
    """Raised when a request conflicts with existing server/resource state."""

    def __init__(
        self,
        message: str = "A resource conflict occurred.",
        code: str = "CONFLICT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=409,
            details=details,
        )


class ValidationError(AppError):
    """Raised when request payload or business validation fails."""

    def __init__(
        self,
        message: str = "Request payload validation failed.",
        code: str = "VALIDATION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=422,
            details=details,
        )


class BusinessRuleError(AppError):
    """Raised when an operation violates a business rule constraint."""

    def __init__(
        self,
        message: str = "Operation violates business rule requirements.",
        code: str = "BUSINESS_RULE_VIOLATION",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=422,
            details=details,
        )


class ExternalServiceError(AppError):
    """Raised when an external third-party integration fails."""

    def __init__(
        self,
        message: str = "An external dependency service error occurred.",
        code: str = "EXTERNAL_SERVICE_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=502,
            details=details,
        )
