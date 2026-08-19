"""Project Service Layer."""

import logging
import re
from math import ceil
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_errors import raise_clean_error_for_write
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectFilter, ProjectResponse, ProjectUpdate
from app.shared.schemas import PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Derive a URL-safe slug from a project name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "project"


class ProjectService:
    """Service for managing project queries and business rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ProjectRepository(session)

    async def create_project(
        self, tenant_id: UUID, created_by: UUID | None, data: ProjectCreate
    ) -> ProjectResponse:
        """Create a new project after validating referenced project_type_id / location_id."""
        await self._validate_project_type(data.project_type_id)
        if data.location_id is not None:
            await self._validate_location(tenant_id, data.location_id)

        slug = data.slug.strip() if data.slug and data.slug.strip() else _slugify(data.name)

        try:
            row = await self.repository.create(tenant_id, created_by, slug, data)
        except DBAPIError as e:
            logger.warning(f"Project create DB error: {e!s}")
            orig_msg = str(getattr(e, "orig", e)).lower()
            if "uq_projects_tenant_slug" in orig_msg or (
                "duplicate key" in orig_msg and "slug" in orig_msg
            ):
                raise ConflictError(
                    message=f"A project with slug '{slug}' already exists for this tenant.",
                    code="DUPLICATE_PROJECT_SLUG",
                ) from e
            raise_clean_error_for_write(e, resource="project")
        return ProjectResponse.model_validate(row)

    async def update_project(
        self, tenant_id: UUID | None, project_id: UUID, data: ProjectUpdate
    ) -> ProjectResponse:
        """Update an existing project after validating any changed references."""
        existing = await self.repository.get_by_id(tenant_id, project_id)
        if not existing:
            raise NotFoundError(
                message=f"Project with ID '{project_id}' was not found.",
                code="PROJECT_NOT_FOUND",
            )

        effective_tenant = tenant_id or existing["tenant_id"]

        if data.project_type_id is not None:
            await self._validate_project_type(data.project_type_id)
        if data.location_id is not None:
            await self._validate_location(effective_tenant, data.location_id)

        try:
            updated_row = await self.repository.update(tenant_id, project_id, data)
        except DBAPIError as e:
            logger.warning(f"Project update DB error: {e!s}")
            raise_clean_error_for_write(e, resource="project")
        if not updated_row:
            raise NotFoundError(
                message=f"Project with ID '{project_id}' was not found.",
                code="PROJECT_NOT_FOUND",
            )
        return ProjectResponse.model_validate(updated_row)

    async def _validate_project_type(self, project_type_id: UUID) -> None:
        """Ensure project_type_id refers to an active project type."""
        result = await self.session.execute(
            text("SELECT id FROM public.project_types WHERE id = :id AND is_active = true"),
            {"id": project_type_id},
        )
        if not result.scalar_one_or_none():
            raise ValidationError(
                message=f"Project type '{project_type_id}' was not found or is inactive.",
                code="PROJECT_TYPE_NOT_FOUND",
            )

    async def _validate_location(self, tenant_id: UUID, location_id: UUID) -> None:
        """Ensure location_id belongs to this tenant."""
        result = await self.session.execute(
            text(
                "SELECT id FROM public.locations WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": location_id, "tenant_id": tenant_id},
        )
        if not result.scalar_one_or_none():
            raise ValidationError(
                message=f"Location '{location_id}' was not found in this tenant.",
                code="LOCATION_NOT_FOUND",
            )

    async def get_project(self, tenant_id: UUID | None, project_id: UUID) -> ProjectResponse:
        """Fetch project details by ID."""
        row = await self.repository.get_by_id(tenant_id, project_id)
        if not row:
            raise NotFoundError(
                message=f"Project with ID '{project_id}' was not found.",
                code="PROJECT_NOT_FOUND",
            )
        return ProjectResponse.model_validate(row)

    async def list_projects(
        self,
        tenant_id: UUID | None,
        filters: ProjectFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[ProjectResponse]:
        """List and search projects."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [ProjectResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[ProjectResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )
