"""Workspace-related Pydantic schemas.

The original NestJS WorkspacesController returns raw `SELECT *` / `RETURNING *`
rows, so the JSON keys are the database column names (snake_case). These schemas
preserve that exact shape (see schema/audit.py for the same convention).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

__all__ = (
    "UpdateInstructionsRequest",
    "WorkspaceInstructionRow",
    "WorkspaceInstructionSummary",
    "WorkspaceRow",
)


class UpdateInstructionsRequest(BaseModel):
    """Body for PUT /workspaces/:id/instructions (was UpdateInstructionsDto)."""

    instructions: str


class WorkspaceInstructionSummary(BaseModel):
    """An active-instruction entry aggregated into each workspace's `list` row."""

    instructions: str
    version: int
    is_active: bool


class WorkspaceRow(BaseModel):
    """A single `workspaces` row as the NestJS `list` endpoint returned it.

    Mirrors `SELECT w.*` plus the aggregated `workspace_instructions` array.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    name: str
    slug: str
    description: str | None = None
    pinecone_partition: str
    created_at: datetime
    updated_at: datetime
    workspace_instructions: list[WorkspaceInstructionSummary] = []


class WorkspaceInstructionRow(BaseModel):
    """A single `workspace_instructions` row, exactly as `updateInstructions` returned it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    instructions: str
    version: int
    is_active: bool
    created_at: datetime
