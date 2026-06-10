"""Organization + membership models.

Mirrors the existing PostgreSQL schema:
  - infrastructure/postgres/migrations/001_auth_core.sql  (organizations)
  - infrastructure/postgres/migrations/002_enterprise_schema.sql
    (organizations.slug, member_role enum, organization_members)

These models are declared with create_type=False / pre-existing columns so they map
onto the live schema without attempting to recreate the Postgres enum.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class MemberRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


# Bind to the existing Postgres enum type `member_role` without trying to (re)create it.
member_role_enum = SAEnum(
    MemberRole,
    name="member_role",
    create_type=False,
    values_callable=lambda e: [member.value for member in e],
)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuid_generate_v4()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    role: Mapped[MemberRole] = mapped_column(member_role_enum, server_default=text("'member'"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("NOW()"))
