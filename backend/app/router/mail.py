"""Mail routes — port of the NestJS MailController.

All routes are mounted under /api/v1 by the parent router and require an
authenticated session (the controller is `@UseGuards(JwtAuthGuard)`), so every
handler depends on CurrentUserDep. Sub-paths, methods and JSON shapes are
preserved exactly so the existing frontend is unaffected.
"""

from typing import Any

from fastapi import APIRouter

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.common import MailClassificationResult
from app.schema.mail import (
    BulkDismissResponse,
    ClassificationIdsRequest,
    DeleteClassificationsResponse,
    GenerateRepliesResponse,
    SaveReplyDraftsRequest,
    SaveReplyDraftsResponse,
)
from app.services.mail_service import MailAgentService

router = APIRouter(prefix="/mail", tags=["mail"])


@router.post("/classify")
async def classify(
    user: CurrentUserDep, db: DBSessionDep
) -> list[MailClassificationResult]:
    return await MailAgentService(db).classify_inbox(user)


@router.get("/classifications")
async def classifications(
    user: CurrentUserDep, db: DBSessionDep
) -> list[dict[str, Any]]:
    return await MailAgentService(db).get_classifications(user)


@router.post("/bulk-dismiss")
async def bulk_dismiss(
    user: CurrentUserDep, db: DBSessionDep
) -> BulkDismissResponse:
    result = await MailAgentService(db).bulk_dismiss_drafts(user)
    return BulkDismissResponse(**result)


@router.post("/classifications/delete")
async def delete_classifications(
    user: CurrentUserDep,
    db: DBSessionDep,
    body: ClassificationIdsRequest | None = None,
) -> DeleteClassificationsResponse:
    ids = body.ids if body else []
    result = await MailAgentService(db).delete_classifications(user, ids)
    return DeleteClassificationsResponse(**result)


@router.post("/reply/generate")
async def generate_replies(
    user: CurrentUserDep,
    db: DBSessionDep,
    body: ClassificationIdsRequest | None = None,
) -> GenerateRepliesResponse:
    ids = body.ids if body else []
    result = await MailAgentService(db).generate_replies(user, ids)
    return GenerateRepliesResponse(**result)


@router.post("/reply/draft")
async def save_reply_drafts(
    user: CurrentUserDep,
    db: DBSessionDep,
    body: SaveReplyDraftsRequest | None = None,
) -> SaveReplyDraftsResponse:
    items = (
        [{"id": i.id, "reply": i.reply} for i in body.items] if body else []
    )
    result = await MailAgentService(db).save_reply_drafts(user, items)
    return SaveReplyDraftsResponse(**result)
