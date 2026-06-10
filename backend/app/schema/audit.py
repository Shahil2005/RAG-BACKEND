"""Audit-related Pydantic schemas.

The original NestJS AuditController returns raw `SELECT *` rows from `audit_logs`,
so the JSON keys are the database column names (snake_case). These schemas preserve
that exact shape.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ("AuditLogRow",)


class AuditLogRow(BaseModel):
    """A single `audit_logs` row, exactly as the NestJS controller returned it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    user_id: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    metadata: dict[str, Any] | None = None
    ip_address: str | None = None
    created_at: datetime
