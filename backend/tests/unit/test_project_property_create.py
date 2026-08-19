"""Unit tests for Project/Property create-update schemas and service validation gaps closed."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.projects.schemas import ProjectCreate, ProjectUpdate
from app.projects.service import ProjectService, _slugify
from app.properties.schemas import PropertyCreate, PropertyUpdate
from app.properties.service import PropertyService


def _ok_check_session() -> AsyncMock:
    """AsyncMock session whose every .execute() call returns a truthy
    scalar_one_or_none() -- i.e. every referenced-row validation check passes."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = uuid4()
    session.execute.return_value = result
    return session


def test_project_create_schema_defaults() -> None:
    """Verify ProjectCreate schema validates required fields and defaults."""
    project = ProjectCreate(
        project_type_id=uuid4(),
        name="Green Meadows",
        city="Jaipur",
        state="Rajasthan",
    )
    assert project.status == "pre_launch"
    assert project.currency == "INR"


def test_slugify_derives_url_safe_slug() -> None:
    """Verify slug auto-derivation from a project name."""
    assert _slugify("Green Meadows Phase 2!") == "green-meadows-phase-2"
    assert _slugify("   ") == "project"


def test_property_create_schema_defaults() -> None:
    """Verify PropertyCreate schema validates required fields and defaults."""
    prop = PropertyCreate(
        project_id=uuid4(),
        property_type_id=uuid4(),
        property_code="P-101",
    )
    assert prop.status == "draft"
    assert prop.area_unit == "sqft"
    assert prop.parking_covered == 0


@pytest.mark.asyncio
async def test_create_project_rejects_unknown_project_type() -> None:
    """Verify project creation validates the referenced project_type_id exists."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    service = ProjectService(session)
    data = ProjectCreate(
        project_type_id=uuid4(), name="Test Project", city="Jaipur", state="Rajasthan"
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.create_project(uuid4(), uuid4(), data)
    assert exc_info.value.code == "PROJECT_TYPE_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_property_rejects_unknown_project() -> None:
    """Verify property creation validates the parent project exists in this tenant."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    service = PropertyService(session)
    data = PropertyCreate(project_id=uuid4(), property_type_id=uuid4(), property_code="P-101")

    with pytest.raises(NotFoundError) as exc_info:
        await service.create_property(uuid4(), uuid4(), data)
    assert exc_info.value.code == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_project_maps_duplicate_slug_db_race_to_conflict() -> None:
    """Verify a slug collision that only manifests at INSERT time (e.g. a
    concurrent request creating a project with the same slug in the gap
    between name-derivation and insert) maps to a clean ConflictError."""
    session = _ok_check_session()
    service = ProjectService(session)
    service.repository.create = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception('duplicate key value violates unique constraint "uq_projects_tenant_slug"'),
        )
    )

    data = ProjectCreate(
        project_type_id=uuid4(), name="Green Meadows", city="Jaipur", state="Rajasthan"
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.create_project(uuid4(), uuid4(), data)
    assert exc_info.value.code == "DUPLICATE_PROJECT_SLUG"


@pytest.mark.asyncio
async def test_create_project_maps_invalid_enum_to_validation_error() -> None:
    """Verify an invalid `status` value (not constrained by Pydantic beyond
    being a string) maps to a clean ValidationError when the DB rejects the
    public.project_status enum cast, instead of a raw 500."""
    session = _ok_check_session()
    service = ProjectService(session)
    service.repository.create = AsyncMock(
        side_effect=DBAPIError(
            "insert", {}, Exception('invalid input value for enum public.project_status: "bogus"')
        )
    )

    data = ProjectCreate(
        project_type_id=uuid4(),
        name="Green Meadows",
        city="Jaipur",
        state="Rajasthan",
        status="bogus",
    )
    with pytest.raises(ValidationError) as exc_info:
        await service.create_project(uuid4(), uuid4(), data)
    assert exc_info.value.code == "INVALID_FIELD_VALUE"


@pytest.mark.asyncio
async def test_update_project_maps_invalid_enum_to_validation_error() -> None:
    """Verify an invalid `status` value on PATCH maps to a clean ValidationError."""
    project_id = uuid4()
    session = _ok_check_session()
    service = ProjectService(session)
    service.repository.get_by_id = AsyncMock(return_value={"id": project_id, "tenant_id": uuid4()})
    service.repository.update = AsyncMock(
        side_effect=DBAPIError(
            "update", {}, Exception('invalid input value for enum public.project_status: "bogus"')
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_project(uuid4(), project_id, ProjectUpdate(status="bogus"))
    assert exc_info.value.code == "INVALID_FIELD_VALUE"


@pytest.mark.asyncio
async def test_create_property_maps_duplicate_code_db_race_to_conflict() -> None:
    """Verify a property_code collision that only manifests at INSERT time
    maps to a clean ConflictError, not a raw IntegrityError."""
    session = _ok_check_session()
    service = PropertyService(session)
    service.repository.create = AsyncMock(
        side_effect=DBAPIError(
            "insert",
            {},
            Exception(
                'duplicate key value violates unique constraint "uq_properties_project_code"'
            ),
        )
    )

    data = PropertyCreate(project_id=uuid4(), property_type_id=uuid4(), property_code="P-101")
    with pytest.raises(ConflictError) as exc_info:
        await service.create_property(uuid4(), uuid4(), data)
    assert exc_info.value.code == "DUPLICATE_PROPERTY_CODE"


@pytest.mark.asyncio
async def test_create_property_maps_invalid_enum_to_validation_error() -> None:
    """Verify an invalid `facing`/`area_unit`/`construction_status` value maps
    to a clean ValidationError when the DB rejects the enum cast."""
    session = _ok_check_session()
    service = PropertyService(session)
    service.repository.create = AsyncMock(
        side_effect=DBAPIError(
            "insert", {}, Exception('invalid input value for enum public.property_facing: "bogus"')
        )
    )

    data = PropertyCreate(
        project_id=uuid4(), property_type_id=uuid4(), property_code="P-101", facing="bogus"
    )
    with pytest.raises(ValidationError) as exc_info:
        await service.create_property(uuid4(), uuid4(), data)
    assert exc_info.value.code == "INVALID_FIELD_VALUE"


@pytest.mark.asyncio
async def test_update_property_maps_invalid_enum_to_validation_error() -> None:
    """Verify an invalid enum value on PATCH (e.g. construction_status) maps
    to a clean ValidationError rather than a raw 500."""
    property_id = uuid4()
    session = _ok_check_session()
    service = PropertyService(session)
    service.repository.get_by_id = AsyncMock(return_value={"id": property_id, "tenant_id": uuid4()})
    service.repository.update = AsyncMock(
        side_effect=DBAPIError(
            "update",
            {},
            Exception('invalid input value for enum public.construction_status: "bogus"'),
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.update_property(
            uuid4(), property_id, PropertyUpdate(construction_status="bogus")
        )
    assert exc_info.value.code == "INVALID_FIELD_VALUE"
