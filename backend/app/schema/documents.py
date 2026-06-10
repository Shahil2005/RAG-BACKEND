"""Documents module Pydantic schemas.

Request models mirror the NestJS DTOs (class-validator) from
``documents.controller.ts``; response models preserve the exact JSON the
controller returned. The shared template/draft types live in
:mod:`app.schema.common` and are re-exported here for convenience.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schema.common import DocumentTemplate, DocumentTemplateType, TemplateVariable

__all__ = (
    "CreateTemplateDto",
    "DeleteTemplateResponse",
    "DocumentTemplate",
    "DocumentTemplateType",
    "DraftEmailDto",
    "DraftEmailResponse",
    "ExportDto",
    "ExportFormat",
    "GenerateDto",
    "GenerateResponse",
    "TemplateVariable",
    "UpdateTemplateDto",
)


# pdf | docx — the two supported export formats (was `ExportFormat` in the controller).
ExportFormat = Literal["pdf", "docx"]


# ---------------------------------------------------------------------------
# Request DTOs (port of the class-validator DTOs)
# ---------------------------------------------------------------------------


class GenerateDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    template_id: str = Field(validation_alias="templateId", serialization_alias="templateId")
    variables: dict[str, str]
    workspace_id: str | None = Field(
        default=None, validation_alias="workspaceId", serialization_alias="workspaceId"
    )


class CreateTemplateDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    type: DocumentTemplateType
    content: str
    variables: list[TemplateVariable] | None = None
    workspace_id: str | None = Field(
        default=None, validation_alias="workspaceId", serialization_alias="workspaceId"
    )


class UpdateTemplateDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    type: DocumentTemplateType | None = None
    content: str | None = None
    variables: list[TemplateVariable] | None = None


class DraftEmailDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    subject: str
    body: str
    to: str | None = None


class ExportDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    content: str
    format: ExportFormat
    filename: str | None = None


# ---------------------------------------------------------------------------
# Response models (preserve the exact controller JSON)
# ---------------------------------------------------------------------------


class GenerateResponse(BaseModel):
    """Body returned by POST /documents/generate."""

    model_config = ConfigDict(populate_by_name=True)

    content: str
    template_id: str = Field(serialization_alias="templateId")
    type: DocumentTemplateType


class DraftEmailResponse(BaseModel):
    """Body returned by POST /documents/draft-email (Graph draft id)."""

    id: str


class DeleteTemplateResponse(BaseModel):
    """Body returned by DELETE /documents/templates/:id."""

    ok: bool = True
