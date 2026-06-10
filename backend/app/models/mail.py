"""Email metadata + classification models.

Mirrors the existing PostgreSQL schema:
  - infrastructure/postgres/migrations/002_enterprise_schema.sql
    (email_metadata, email_category enum, email_classifications)
  - infrastructure/postgres/migrations/007_email_sent_category.sql
    (adds the 'sent' value to the email_category enum)

Owned by the mail module. The Postgres ``email_category`` enum is bound with
create_type=False so the model maps onto the live schema without recreating it.
UUID PKs and TIMESTAMPTZ defaults are declared with server defaults to match the
existing tables exactly.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

# email_metadata is owned by the ingestion module; re-export to avoid a duplicate
# Table registration on Base.metadata (both modules mirror the same migration).
from app.models.ingestion import EmailMetadata  # noqa: F401

from . import Base


class EmailCategory(str, enum.Enum):
    important = "important"
    spam = "spam"
    closed = "closed"
    pending_action = "pending_action"
    sent = "sent"


# Bind to the existing Postgres enum type `email_category` without (re)creating it.
email_category_enum = SAEnum(
    EmailCategory,
    name="email_category",
    create_type=False,
    values_callable=lambda e: [member.value for member in e],
)


class EmailClassification(Base):
    __tablename__ = "email_classifications"
    __table_args__ = (UniqueConstraint("email_metadata_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    email_metadata_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("email_metadata.id", ondelete="CASCADE")
    )
    category: Mapped[EmailCategory] = mapped_column(email_category_enum)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
