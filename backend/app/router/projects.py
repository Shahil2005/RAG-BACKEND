"""Projects routes — port of the NestJS ProjectsController.

Mounted under /api/v1 by the parent router, so paths match the original
``@Controller('projects')`` exactly. Every route required ``JwtAuthGuard`` in
NestJS, so each handler takes :data:`CurrentUserDep`.
"""

from fastapi import APIRouter, File, Form, UploadFile, status

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.common import ProjectDetail, ProjectFileSummary, ProjectSector, ProjectSummary
from app.schema.projects import (
    AssignSectorRequest,
    CreateProjectRequest,
    CreateSectorRequest,
    UpdateProjectRequest,
)
from app.services.projects_service import ProjectFilesService, ProjectsService

router = APIRouter(prefix="/projects", tags=["projects"])

# Max upload size enforced by the NestJS FileInterceptor (5 MiB).
_MAX_UPLOAD_BYTES = 5_242_880


@router.get("")
async def list_projects(
    user: CurrentUserDep, db: DBSessionDep
) -> list[ProjectSummary]:
    return await ProjectsService(db).list(user)


@router.post("")
async def create_project(
    user: CurrentUserDep, db: DBSessionDep, body: CreateProjectRequest
) -> ProjectSummary:
    return await ProjectsService(db).create(user, body)


@router.get("/{id}")
async def get_project(
    user: CurrentUserDep, db: DBSessionDep, id: str
) -> ProjectDetail:
    return await ProjectsService(db).get_by_id(user, id)


@router.patch("/{id}")
async def update_project(
    user: CurrentUserDep, db: DBSessionDep, id: str, body: UpdateProjectRequest
) -> ProjectSummary:
    return await ProjectsService(db).update(user, id, body)


@router.delete("/{id}", status_code=status.HTTP_200_OK)
async def delete_project(user: CurrentUserDep, db: DBSessionDep, id: str) -> None:
    await ProjectsService(db).remove(user, id)


@router.get("/{id}/sectors")
async def list_sectors(
    user: CurrentUserDep, db: DBSessionDep, id: str
) -> list[ProjectSector]:
    return await ProjectsService(db).list_sectors(user, id)


@router.post("/{id}/sectors")
async def create_sector(
    user: CurrentUserDep, db: DBSessionDep, id: str, body: CreateSectorRequest
) -> ProjectSector:
    return await ProjectsService(db).create_sector(user, id, body.name)


@router.delete("/{id}/sectors/{sector_id}", status_code=status.HTTP_200_OK)
async def delete_sector(
    user: CurrentUserDep, db: DBSessionDep, id: str, sector_id: str
) -> None:
    await ProjectsService(db).delete_sector(user, id, sector_id)


@router.get("/{id}/files")
async def list_files(
    user: CurrentUserDep, db: DBSessionDep, id: str
) -> list[ProjectFileSummary]:
    return await ProjectFilesService(db).list_files(user, id)


@router.post("/{id}/files")
async def upload_file(
    user: CurrentUserDep,
    db: DBSessionDep,
    id: str,
    file: UploadFile = File(...),
    sector_id: str | None = Form(default=None, alias="sectorId"),
) -> ProjectFileSummary:
    buffer = await file.read()
    return await ProjectFilesService(db).upload_and_index(
        user,
        id,
        buffer,
        file.filename,
        file.content_type,
        len(buffer),
        sector_id or None,
    )


@router.patch("/{id}/files/{file_id}/sector")
async def assign_file_sector(
    user: CurrentUserDep,
    db: DBSessionDep,
    id: str,
    file_id: str,
    body: AssignSectorRequest,
) -> ProjectFileSummary:
    return await ProjectFilesService(db).assign_sector(
        user, id, file_id, body.sector_id or None
    )


@router.delete("/{id}/files/{file_id}", status_code=status.HTTP_200_OK)
async def delete_file(
    user: CurrentUserDep, db: DBSessionDep, id: str, file_id: str
) -> None:
    await ProjectFilesService(db).delete_file(user, id, file_id)
