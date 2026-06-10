"""Audit log model.

Mirrors the existing PostgreSQL schema:
  - infrastructure/postgres/migrations/002_enterprise_schema.sql  (audit_logs)

Owned by the audit module. Columns mirror the live table precisely; UUID PK and
TIMESTAMPTZ default are declared with server defaults so the model maps onto the
existing schema without recreating it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text, Uuid, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'"), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
