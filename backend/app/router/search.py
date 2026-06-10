"""Search routes — port of the NestJS SearchController.

Mounted under /api/v1 by the parent router. Both routes were protected with
@UseGuards(JwtAuthGuard) and so require an authenticated user (CurrentUserDep).
"""

from fastapi import APIRouter

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.common import UnifiedSearchRequest, UnifiedSearchResponse
from app.schema.search import InternetSearchDto, UnifiedSearchDto
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/unified")
async def unified(
    user: CurrentUserDep, db: DBSessionDep, body: UnifiedSearchDto
) -> UnifiedSearchResponse:
    request = UnifiedSearchRequest(
        query=body.query,
        workspace_id=body.workspace_id,
        sources=body.sources,
        top_k=body.top_k,
    )
    return await SearchService(db).unified_search(user, request)


@router.post("/internet")
async def internet(
    user: CurrentUserDep, db: DBSessionDep, body: InternetSearchDto
) -> UnifiedSearchResponse:
    return await SearchService(db).internet_search(user, body.query)
