"""Chat session + message models.

Mirrors the existing PostgreSQL schema:
  - infrastructure/postgres/migrations/002_enterprise_schema.sql  (chat_sessions, chat_messages)
  - infrastructure/postgres/migrations/005_projects.sql           (chat_sessions.project_id)

The chat module owns these two tables. `chat_messages.role` is a plain TEXT column with a
CHECK constraint ('user'/'assistant'/'system') in Postgres — not a native enum — so it is
mapped as Text here. `citations`/`metadata` are JSONB columns.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[Any] | None] = mapped_column(
        JSONB, server_default=text("'[]'"), nullable=True
    )
    message_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
