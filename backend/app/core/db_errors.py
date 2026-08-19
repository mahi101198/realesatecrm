"""Shared helper for mapping raw SQLAlchemy/Postgres write exceptions to
clean, typed AppError subclasses.

Used by the new property-lifecycle domains (locations, projects, properties,
bookings, sales/payments, ownerships/co-owners/resale-listings) at every
INSERT/UPDATE that accepts a user-supplied enum-cast string or references
another tenant-owned row, so a malformed value or a violated DB constraint
never reaches the client as a raw, unhandled 500.

Deliberately narrow: only recognizes specific, well-understood Postgres error
shapes (invalid enum literal, CHECK violation, FK violation, unique
violation). Anything else is re-raised unchanged, so it falls through to
app/core/error_handlers.py's unhandled_exception_handler -- the correct
last-resort net for genuinely unexpected failures (a dropped connection, a
schema drift, anything not anticipated here). This module must never become
a second catch-all that hides real bugs behind a misleadingly specific error
code.
"""

import logging
from typing import NoReturn

from sqlalchemy.exc import DBAPIError

from app.core.exceptions import ConflictError, ValidationError

logger = logging.getLogger(__name__)


def raise_clean_error_for_write(e: DBAPIError, *, resource: str) -> NoReturn:
    """Inspect a DB write exception and raise the matching clean AppError.

    Always raises. Callers use it from an except block:

        except DBAPIError as e:
            raise_clean_error_for_write(e, resource="property") from e

    `resource` is a short, human-readable noun (e.g. "property", "payment")
    used to build a specific-enough message without echoing raw DB internals.
    """
    orig_msg = str(getattr(e, "orig", e)).lower()

    if "invalid input value for enum" in orig_msg or "invalid input syntax" in orig_msg:
        raise ValidationError(
            message=f"One or more fields contain an invalid value for this {resource}.",
            code="INVALID_FIELD_VALUE",
        ) from e

    if "violates check constraint" in orig_msg:
        raise ValidationError(
            message=f"One or more fields are outside the allowed range for this {resource}.",
            code="CHECK_CONSTRAINT_VIOLATION",
        ) from e

    if "violates not-null constraint" in orig_msg:
        raise ValidationError(
            message=f"A required field is missing for this {resource}.",
            code="MISSING_REQUIRED_FIELD",
        ) from e

    if "violates foreign key constraint" in orig_msg:
        raise ValidationError(
            message=(
                f"A record referenced by this {resource} does not exist "
                "or is not accessible in this tenant."
            ),
            code="INVALID_REFERENCE",
        ) from e

    if "violates unique constraint" in orig_msg or "duplicate key value" in orig_msg:
        raise ConflictError(
            message=f"A conflicting {resource} record already exists.",
            code="DUPLICATE_RECORD",
        ) from e

    # Unrecognized shape -- do not guess. Re-raise so it hits the generic
    # unhandled_exception_handler, which logs full details server-side and
    # returns a safe, request-id-bearing generic message to the client.
    logger.error(f"Unrecognized DB write error for resource '{resource}': {orig_msg}")
    raise e
