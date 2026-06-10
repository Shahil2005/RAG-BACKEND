"""Microsoft Graph routes — port of the NestJS GraphController.

Paths/methods mirror the controller (mounted under /api/v1 by the parent router):
  POST /graph/sync   -> persist the caller's Microsoft provider tokens
  GET  /graph/status -> connectivity probe (best-effort, never raises)
Both require an authenticated session (was @UseGuards(JwtAuthGuard)).
"""

from fastapi import APIRouter

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.graph import GraphSyncRequest, StatusResponse, SyncResponse
from app.services.graph_service import GraphService

router = APIRouter(prefix="/graph", tags=["graph"])


@router.post("/sync")
async def sync(
    body: GraphSyncRequest, user: CurrentUserDep, db: DBSessionDep
) -> SyncResponse:
    await GraphService(db).sync_from_provider_tokens(
        user.user_id,
        body.provider_access_token,
        body.provider_refresh_token,
    )
    return SyncResponse(synced=True)


@router.get("/status")
async def status(user: CurrentUserDep, db: DBSessionDep) -> StatusResponse:
    try:
        await GraphService(db).fetch_recent_emails(user, 1)
        return StatusResponse(connected=True)
    except Exception:
        return StatusResponse(connected=False)
