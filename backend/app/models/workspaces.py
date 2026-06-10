"""Workspace + workspace-instructions models.

Mirrors the existing PostgreSQL schema:
  - infrastructure/postgres/migrations/001_auth_core.sql        (workspaces base columns)
  - infrastructure/postgres/migrations/002_enterprise_schema.sql
    (workspaces.slug/description/pinecone_partition/created_at/updated_at,
     workspace_instructions)

Owned by the workspaces module. Columns mirror the live table precisely; UUID PK
and TIMESTAMPTZ defaults are declared with server defaults so the models map onto
the existing schema without recreating it.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("organization_id", "slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinecone_partition: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))


class WorkspaceInstruction(Base):
    __tablename__ = "workspace_instructions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    instructions: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("TRUE"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
