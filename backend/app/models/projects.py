"""Project, project-sector and project-file models.

Mirrors the existing PostgreSQL schema:
  - infrastructure/postgres/migrations/005_projects.sql (projects, project_files)
  - infrastructure/postgres/migrations/006_project_sectors.sql
    (project_sectors, project_files.sector_id)

A project is a per-user knowledge base scoped to an organization. Uploaded
``project_files`` are extracted, chunked and indexed into Pinecone; optional
``project_sectors`` group those files so chat retrieval can be scoped to a sector.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))


class ProjectSector(Base):
    __tablename__ = "project_sectors"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))


class ProjectFile(Base):
    __tablename__ = "project_files"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    file_name: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_indexed: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    indexed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    index_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    sector_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("project_sectors.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
