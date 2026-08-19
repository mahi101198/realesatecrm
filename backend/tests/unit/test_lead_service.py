"""Unit tests for Lead domain schemas and business validation."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.leads.schemas import LeadCreate, LeadPropertyInterestCreate


def test_lead_create_schema() -> None:
    """Verify LeadCreate validates score range and budget limits."""
    cust_id = uuid4()
    lead_data = LeadCreate(
        customer_id=cust_id,
        budget_min=Decimal("5000000"),
        budget_max=Decimal("10000000"),
        preferred_city="Gurgaon",
        lead_score=85,
    )
    assert lead_data.customer_id == cust_id
    assert lead_data.budget_min == Decimal("5000000")
    assert lead_data.lead_score == 85

    with pytest.raises(ValidationError):
        LeadCreate(
            customer_id=cust_id,
            lead_score=150,  # exceeds max 100
        )


def test_lead_property_interest_schema() -> None:
    """Verify property interest schema validation."""
    proj_id = uuid4()
    prop_id = uuid4()
    interest = LeadPropertyInterestCreate(
        project_id=proj_id,
        property_id=prop_id,
        interest_level="high",
        is_primary=True,
    )
    assert interest.project_id == proj_id
    assert interest.is_primary is True
