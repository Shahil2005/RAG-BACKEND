"""Documents routes — port of the NestJS DocumentsController.

Route sub-paths, methods and JSON shapes are preserved exactly (mounted under
/api/v1 by the parent router) so the existing frontend is unaffected. Every route
was guarded by ``@UseGuards(JwtAuthGuard)`` and is therefore protected with
``CurrentUserDep``.
"""

import re

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.common import DocumentTemplate
from app.schema.documents import (
    CreateTemplateDto,
    DeleteTemplateResponse,
    DraftEmailDto,
    DraftEmailResponse,
    ExportDto,
    GenerateDto,
    GenerateResponse,
    UpdateTemplateDto,
)
from app.services.documents_service import (
    DocumentBadRequestError,
    DocumentExportService,
    DocumentNotFoundError,
    DocumentsService,
)

router = APIRouter(prefix="/documents", tags=["documents"])

# pdf | docx -> response Content-Type (port of EXPORT_MIME).
_EXPORT_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_SAFE_NAME_RE = re.compile(r"[^a-z0-9\-_]+", re.IGNORECASE)


def _to_http(err: DocumentBadRequestError | DocumentNotFoundError) -> HTTPException:
    if isinstance(err, DocumentNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))


@router.get("/templates")
async def templates(user: CurrentUserDep, db: DBSessionDep) -> list[DocumentTemplate]:
    return await DocumentsService(db).list_templates(user)


@router.post("/templates")
async def create_template(
    user: CurrentUserDep, db: DBSessionDep, body: CreateTemplateDto
) -> DocumentTemplate:
    try:
        return await DocumentsService(db).create_template(
            user,
            {
                "name": body.name,
                "type": body.type,
                "content": body.content,
                "variables": body.variables,
                "workspace_id": body.workspace_id,
            },
        )
    except (DocumentBadRequestError, DocumentNotFoundError) as err:
        raise _to_http(err) from err


@router.patch("/templates/{id}")
async def update_template(
    user: CurrentUserDep, db: DBSessionDep, id: str, body: UpdateTemplateDto
) -> DocumentTemplate:
    try:
        return await DocumentsService(db).update_template(
            user,
            id,
            {
                "name": body.name,
                "type": body.type,
                "content": body.content,
                "variables": body.variables,
            },
        )
    except (DocumentBadRequestError, DocumentNotFoundError) as err:
        raise _to_http(err) from err


@router.delete("/templates/{id}")
async def delete_template(
    user: CurrentUserDep, db: DBSessionDep, id: str
) -> DeleteTemplateResponse:
    try:
        await DocumentsService(db).delete_template(user, id)
    except (DocumentBadRequestError, DocumentNotFoundError) as err:
        raise _to_http(err) from err
    return DeleteTemplateResponse(ok=True)


@router.post("/generate")
async def generate(
    user: CurrentUserDep, db: DBSessionDep, body: GenerateDto
) -> GenerateResponse:
    try:
        result = await DocumentsService(db).generate(
            user,
            {"template_id": body.template_id, "variables": body.variables},
        )
    except (DocumentBadRequestError, DocumentNotFoundError) as err:
        raise _to_http(err) from err
    return GenerateResponse(
        content=result["content"],
        template_id=result["template_id"],
        type=result["type"],
    )


@router.post("/draft-email")
async def draft_email(
    user: CurrentUserDep, db: DBSessionDep, body: DraftEmailDto
) -> DraftEmailResponse:
    try:
        result = await DocumentsService(db).save_email_draft(
            user, {"subject": body.subject, "body": body.body, "to": body.to}
        )
    except (DocumentBadRequestError, DocumentNotFoundError) as err:
        raise _to_http(err) from err
    return DraftEmailResponse(id=result["id"])


@router.post("/export")
async def export(user: CurrentUserDep, db: DBSessionDep, body: ExportDto) -> Response:
    # export_ai runs a dedicated layout LLM call (proper tables), falling back to
    # the deterministic markdown renderer if the model/key is unavailable.
    buffer = await DocumentExportService().export_ai(body.content, body.format)
    safe_name = _SAFE_NAME_RE.sub("_", body.filename or "document")[:80] or "document"
    return Response(
        content=buffer,
        media_type=_EXPORT_MIME[body.format],
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.{body.format}"'
        },
    )
