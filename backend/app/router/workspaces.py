"""Workspace routes — port of the NestJS WorkspacesController.

Route sub-paths/methods and JSON shapes are preserved exactly (mounted under
/api/v1 by the parent router). All routes were guarded by @UseGuards(JwtAuthGuard)
and so require CurrentUserDep.
"""

from fastapi import APIRouter

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.workspaces import UpdateInstructionsRequest, WorkspaceInstructionRow, WorkspaceRow
from app.services.workspaces_service import WorkspacesService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("")
async def list_workspaces(
    user: CurrentUserDep, db: DBSessionDep
) -> list[WorkspaceRow]:
    return await WorkspacesService(db).list(user)


@router.put("/{id}/instructions")
async def update_instructions(
    id: str,
    body: UpdateInstructionsRequest,
    user: CurrentUserDep,
    db: DBSessionDep,
) -> WorkspaceInstructionRow:
    return await WorkspacesService(db).update_instructions(user, id, body.instructions)
