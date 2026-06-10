"""Ingestion module models.

Mirrors the existing PostgreSQL schema:
  - infrastructure/postgres/migrations/002_enterprise_schema.sql
    (email_metadata, file_metadata + file_source enum, ingestion_jobs +
    ingestion_status enum)
  - infrastructure/postgres/migrations/003_file_metadata_drive.sql
    (file_metadata.drive_id / site_id / mime_type)
  - infrastructure/postgres/migrations/004_sync_cursors_index_reason.sql
    (sync_cursors, file_metadata.index_reason)

These tables are owned by the ingestion module. UUID PKs use
``uuid_generate_v4()`` and TIMESTAMPTZ columns default to ``NOW()`` via server
defaults so the models map onto the live schema without recreating it. The
Postgres enums ``file_source`` and ``ingestion_status`` are bound with
``create_type=False``.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Text, Uuid, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class FileSource(str, enum.Enum):
    sharepoint = "sharepoint"
    onedrive = "onedrive"


# Bind to the existing Postgres enum `file_source` without (re)creating it.
file_source_enum = SAEnum(
    FileSource,
    name="file_source",
    create_type=False,
    values_callable=lambda e: [member.value for member in e],
)


class IngestionStatusEnum(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


# Bind to the existing Postgres enum `ingestion_status` without (re)creating it.
ingestion_status_enum = SAEnum(
    IngestionStatusEnum,
    name="ingestion_status",
    create_type=False,
    values_callable=lambda e: [member.value for member in e],
)


class EmailMetadata(Base):
    __tablename__ = "email_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    graph_message_id: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_indexed: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    indexed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )


class FileMetadata(Base):
    __tablename__ = "file_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    graph_item_id: Mapped[str] = mapped_column(Text)
    source: Mapped[FileSource] = mapped_column(file_source_enum)
    file_name: Mapped[str] = mapped_column(Text)
    web_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    modified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    is_indexed: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    indexed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )
    # Added by migration 003_file_metadata_drive.sql
    drive_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Added by migration 004_sync_cursors_index_reason.sql
    index_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    job_type: Mapped[str] = mapped_column(Text)
    status: Mapped[IngestionStatusEnum] = mapped_column(
        ingestion_status_enum, server_default=text("'pending'")
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, server_default=text("'{}'"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )


class SyncCursor(Base):
    """Delta-sync cursors per user/drive/source (migration 004)."""

    __tablename__ = "sync_cursors"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    drive_id: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("''"))
    delta_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("NOW()")
    )
