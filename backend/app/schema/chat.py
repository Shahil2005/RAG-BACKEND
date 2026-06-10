"""Chat-related Pydantic schemas.

Request DTOs mirror the NestJS chat.controller.ts class-validator DTOs
(CreateSessionDto / SendMessageDto). Response shapes reuse the shared types from
app.schema.common (ChatSessionSummary, ChatMessageRecord, RagQueryResponse) so the
exact JSON returned to the frontend is preserved.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schema.common import ChatMessageRecord, ChatSessionSummary, RagQueryResponse

__all__ = (
    "ChatMessageRecord",
    "ChatSessionSummary",
    "CreateSessionRequest",
    "DeleteSessionResponse",
    "RagQueryResponse",
    "SendMessageRequest",
)


class CreateSessionRequest(BaseModel):
    """Port of CreateSessionDto — all fields optional.

    Accepts the camelCase keys the frontend sends (workspaceId / projectId).
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    project_id: str | None = Field(default=None, alias="projectId")


class SendMessageRequest(BaseModel):
    """Port of SendMessageDto — `content` is required.

    Accepts the camelCase keys the frontend sends (workspaceId / sectorId).
    """

    model_config = ConfigDict(populate_by_name=True)

    content: str
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    sector_id: str | None = Field(default=None, alias="sectorId")


class DeleteSessionResponse(BaseModel):
    """Matches the NestJS deleteSession return shape: { ok: true }."""

    ok: bool = True
