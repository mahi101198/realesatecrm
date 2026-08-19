"""Shared Domain Type Aliases."""

from typing import Any, NewType
from uuid import UUID

# Primary entity ID types for type-safety across services
TenantID = NewType("TenantID", UUID)
UserID = NewType("UserID", UUID)
CustomerID = NewType("CustomerID", UUID)
LeadID = NewType("LeadID", UUID)
PropertyID = NewType("PropertyID", UUID)
CallID = NewType("CallID", UUID)

# General JSON representation dictionary type
JsonDict = dict[str, Any]
