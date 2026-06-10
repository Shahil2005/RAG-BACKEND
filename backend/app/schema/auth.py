"""Auth-related Pydantic schemas.

Response field aliases preserve the exact camelCase JSON shape returned by the
original NestJS API so the frontend keeps working unchanged
(e.g. AuthContext -> {"userId": ..., "organizationId": ..., "role": ...}).
"""

from pydantic import BaseModel, ConfigDict, Field

from app.models.organization import MemberRole

__all__ = (
    "AuthContext",
    "AuthorizeUrlResponse",
    "GraphProfile",
    "LogoutResponse",
    "MeResponse",
    "MemberRole",
    "SessionResponse",
)


class AuthContext(BaseModel):
    """Per-request authenticated user context (was @starbot/types AuthContext)."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    user_id: str = Field(serialization_alias="userId")
    organization_id: str = Field(serialization_alias="organizationId")
    role: MemberRole
    email: str | None = None


class MeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    email: str
    name: str | None = None
    avatar: str | None = None
    organization_id: str | None = Field(default=None, serialization_alias="organizationId")
    role: MemberRole | None = None


class SessionResponse(BaseModel):
    user: AuthContext


class AuthorizeUrlResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    authorize_url: str = Field(serialization_alias="authorizeUrl")


class LogoutResponse(BaseModel):
    ok: bool = True


class GraphProfile(BaseModel):
    """Subset of the Microsoft Graph /me payload used during login."""

    model_config = ConfigDict(extra="ignore")

    id: str
    displayName: str | None = None  # noqa: N815 (Graph API field name)
    mail: str | None = None
    userPrincipalName: str | None = None  # noqa: N815 (Graph API field name)
