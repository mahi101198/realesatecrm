"""Unit tests for app/core/db_errors.py's shared DB-write-exception mapping,
used across every new property-lifecycle domain (locations, projects,
properties, bookings, sales/payments, ownerships/co-owners/resale-listings)
to turn predictable Postgres failures into clean, typed AppError responses."""

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.db_errors import raise_clean_error_for_write
from app.core.exceptions import ConflictError, ValidationError


def _dbapi_error(message: str) -> DBAPIError:
    return DBAPIError("statement", {}, Exception(message))


def test_invalid_enum_value_maps_to_validation_error() -> None:
    """Verify an invalid enum literal (e.g. a bad status string) maps to a
    clean ValidationError, not a raw 500."""
    e = _dbapi_error('invalid input value for enum public.property_status: "bogus"')
    with pytest.raises(ValidationError) as exc_info:
        raise_clean_error_for_write(e, resource="property")
    assert exc_info.value.code == "INVALID_FIELD_VALUE"
    assert exc_info.value.status_code == 422


def test_invalid_input_syntax_maps_to_validation_error() -> None:
    """Verify a malformed literal (e.g. bad numeric/date syntax) maps cleanly."""
    e = _dbapi_error('invalid input syntax for type numeric: "abc"')
    with pytest.raises(ValidationError) as exc_info:
        raise_clean_error_for_write(e, resource="payment")
    assert exc_info.value.code == "INVALID_FIELD_VALUE"


def test_check_constraint_violation_maps_to_validation_error() -> None:
    """Verify a violated CHECK constraint maps to a clean ValidationError."""
    e = _dbapi_error('new row for relation "properties" violates check constraint "chk_x"')
    with pytest.raises(ValidationError) as exc_info:
        raise_clean_error_for_write(e, resource="property")
    assert exc_info.value.code == "CHECK_CONSTRAINT_VIOLATION"


def test_not_null_violation_maps_to_validation_error() -> None:
    """Verify a violated NOT NULL constraint maps to a clean ValidationError."""
    e = _dbapi_error('null value in column "x" violates not-null constraint')
    with pytest.raises(ValidationError) as exc_info:
        raise_clean_error_for_write(e, resource="booking")
    assert exc_info.value.code == "MISSING_REQUIRED_FIELD"


def test_foreign_key_violation_maps_to_validation_error() -> None:
    """Verify a violated FK constraint (referencing a deleted/cross-tenant row
    that slipped past an application-level check) maps to a clean
    ValidationError rather than a raw 500."""
    e = _dbapi_error('insert or update on table "x" violates foreign key constraint "fk_y"')
    with pytest.raises(ValidationError) as exc_info:
        raise_clean_error_for_write(e, resource="ownership record")
    assert exc_info.value.code == "INVALID_REFERENCE"


def test_unique_violation_maps_to_conflict_error() -> None:
    """Verify a violated unique constraint maps to a clean ConflictError."""
    e = _dbapi_error('duplicate key value violates unique constraint "uq_x"')
    with pytest.raises(ConflictError) as exc_info:
        raise_clean_error_for_write(e, resource="resale listing")
    assert exc_info.value.code == "DUPLICATE_RECORD"
    assert exc_info.value.status_code == 409


def test_unrecognized_error_is_reraised_unchanged() -> None:
    """Verify a genuinely unrecognized DB error shape is NOT swallowed into a
    misleading typed error -- it must propagate as-is, so it still reaches
    app/core/error_handlers.py's unhandled_exception_handler (the correct
    last-resort net for truly unexpected failures)."""
    e = _dbapi_error("server closed the connection unexpectedly")
    with pytest.raises(DBAPIError):
        raise_clean_error_for_write(e, resource="property")
