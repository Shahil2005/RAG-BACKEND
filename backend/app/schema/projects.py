"""Projects request/response schemas.

Response shapes (``ProjectSummary``, ``ProjectSector``, ``ProjectFileSummary``,
``ProjectDetail``) are the shared workspace types and live in
:mod:`app.schema.common`; they are re-exported here for convenience. This module
adds the request DTOs that mirror the ``class-validator`` DTOs on the NestJS
``ProjectsController`` (CreateProjectDto / UpdateProjectDto / CreateSectorDto /
AssignSectorDto).
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schema.common import ProjectDetail, ProjectFileSummary, ProjectSector, ProjectSummary

__all__ = (
    "AssignSectorRequest",
    "CreateProjectRequest",
    "CreateSectorRequest",
    "ProjectDetail",
    "ProjectFileSummary",
    "ProjectSector",
    "ProjectSummary",
    "UpdateProjectRequest",
)


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str | None = None
    custom_instructions: str | None = Field(default=None, validation_alias="customInstructions")


class UpdateProjectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    custom_instructions: str | None = Field(default=None, validation_alias="customInstructions")


class CreateSectorRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str


class AssignSectorRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sector_id: str | None = Field(default=None, validation_alias="sectorId")
