"""Shared API Request and Response Schemas."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):  # noqa: UP046
    """Standard pagination contract for all CRM list endpoints."""

    items: list[T]
    page: int = Field(..., ge=1, description="1-based page index")
    page_size: int = Field(..., ge=1, le=100, description="Items per page")
    total: int = Field(..., ge=0, description="Total matching items count")
    pages: int = Field(..., ge=0, description="Total pages count")


class PaginationParams(BaseModel):
    """Standard pagination request query parameters."""

    page: int = Field(default=1, ge=1, description="1-based page index")
    page_size: int = Field(default=25, ge=1, le=100, description="Items per page (max 100)")

    @property
    def offset(self) -> int:
        """Calculate SQL OFFSET value."""
        return (self.page - 1) * self.page_size
