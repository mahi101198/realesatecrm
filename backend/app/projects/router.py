"""Project REST API Router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.projects.schemas import ProjectCreate, ProjectFilter, ProjectResponse, ProjectUpdate
from app.projects.service import ProjectService
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Project",
    description="Create a new real estate development project.",
)
async def create_project(
    data: ProjectCreate,
    context: RequestContext = Depends(require_permission(Permission.PROJECT_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectResponse:
    """Create project endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a project.")
    service = ProjectService(session)
    return await service.create_project(tenant_id, context.user_id, data)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Project Details",
    description="Fetch details of a single real estate project by ID.",
)
async def get_project(
    project_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectResponse:
    """Get project details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = ProjectService(session)
    project = await service.get_project(tenant_id, project_id)
    ensure_tenant_resource_access(context, project.tenant_id)
    return project


@router.get(
    "",
    response_model=PaginatedResponse[ProjectResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Projects",
    description="List and filter real estate projects with pagination.",
)
async def list_projects(
    city: Annotated[str | None, Query(description="Filter by city")] = None,
    project_status: Annotated[
        str | None, Query(alias="status", description="Filter by project status")
    ] = None,
    project_type_id: Annotated[UUID | None, Query(description="Filter by project type")] = None,
    is_featured: Annotated[bool | None, Query(description="Filter featured projects")] = None,
    query: Annotated[
        str | None, Query(description="Search project name, developer, locality")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[ProjectResponse]:
    """List projects endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = ProjectFilter(
        city=city,
        status=project_status,
        project_type_id=project_type_id,
        is_featured=is_featured,
        query=query,
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = ProjectService(session)
    return await service.list_projects(tenant_id, filters, pagination)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Project",
    description="Update an existing real estate development project.",
)
async def update_project(
    project_id: UUID,
    data: ProjectUpdate,
    context: RequestContext = Depends(require_permission(Permission.PROJECT_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectResponse:
    """Update project endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = ProjectService(session)
    project = await service.get_project(tenant_id, project_id)
    ensure_tenant_resource_access(context, project.tenant_id)
    return await service.update_project(tenant_id, project_id, data)
