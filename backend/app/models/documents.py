"""Document template model.

Mirrors the existing PostgreSQL schema:
  - infrastructure/postgres/migrations/002_enterprise_schema.sql  (document_templates,
    template_type enum)
  - infrastructure/postgres/migrations/008_document_templates_seed.sql  (user_id,
    is_default authoring columns)

Owned by the documents module. Columns mirror the live table precisely; the UUID PK
and TIMESTAMPTZ defaults are declared with server defaults, and the Postgres enum
``template_type`` is bound with ``create_type=False`` so the model maps onto the
existing schema without recreating it.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class TemplateType(str, enum.Enum):
    estimate = "estimate"
    job_summary = "job_summary"
    report = "report"
    quotation = "quotation"
    customer_email = "customer_email"


# Bind to the existing Postgres enum type `template_type` without trying to (re)create it.
template_type_enum = SAEnum(
    TemplateType,
    name="template_type",
    create_type=False,
    values_callable=lambda e: [member.value for member in e],
)


class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[TemplateType] = mapped_column(template_type_enum)
    content: Mapped[str] = mapped_column(Text)
    variables: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'")
    )
    # Original 002 column (kept for schema parity; the service writes user_id instead).
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    # Added in 008: the authoring user and the per-type default marker.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_default: Mapped[bool] = mapped_column(server_default=text("FALSE"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
