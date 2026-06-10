"""Mail module Pydantic schemas.

Port of the request/response shapes used by the NestJS MailController +
MailAgentService. Response field aliases preserve the EXACT JSON the NestJS API
returned so the frontend keeps working unchanged.

The classification result / row shapes already live in app.schema.common
(MailClassificationResult, MailClassificationRow); this module only adds the
request DTOs and the reply/draft/dismiss/delete response shapes.
"""

from pydantic import BaseModel, ConfigDict, Field

# Re-export the shared classification shapes for convenience.
from app.schema.common import EmailCategory, MailClassificationResult, MailClassificationRow

__all__ = (
    "BulkDismissResponse",
    "ClassificationIdsRequest",
    "DeleteClassificationsResponse",
    "EmailCategory",
    "GenerateRepliesResponse",
    "GeneratedReply",
    "MailClassificationResult",
    "MailClassificationRow",
    "ReplyDraftItem",
    "SaveReplyDraftsRequest",
    "SaveReplyDraftsResponse",
)


# --- Request DTOs ----------------------------------------------------------


class ClassificationIdsRequest(BaseModel):
    """Body for POST /mail/classifications/delete and /mail/reply/generate.

    The NestJS controller reads ``body?.ids ?? []`` so the field is optional and
    defaults to an empty list.
    """

    model_config = ConfigDict(populate_by_name=True)

    ids: list[str] = Field(default_factory=list)


class ReplyDraftItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    reply: str


class SaveReplyDraftsRequest(BaseModel):
    """Body for POST /mail/reply/draft (``body?.items ?? []``)."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[ReplyDraftItem] = Field(default_factory=list)


# --- Response shapes -------------------------------------------------------


class GeneratedReply(BaseModel):
    """A single AI-generated reply (port of the GeneratedReply interface)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    subject: str
    sender: str
    reply: str


class BulkDismissResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dismissed: int


class DeleteClassificationsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    requested: int
    deleted: int
    failed: list[str] = Field(default_factory=list)


class GenerateRepliesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    replies: list[GeneratedReply] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)


class SaveReplyDraftsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    created: int
    failed: list[str] = Field(default_factory=list)
