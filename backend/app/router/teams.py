"""Teams routes — port of the NestJS TeamsController.

Mounted under /api/v1 by the parent router. The single route mirrors the original
@Controller('teams') + @UseGuards(JwtAuthGuard) POST /sync endpoint.
"""

from fastapi import APIRouter

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.teams import TeamsSyncResult
from app.services.teams_service import TeamsIngestionService

router = APIRouter(prefix="/teams", tags=["teams"])


@router.post("/sync")
async def sync(user: CurrentUserDep, db: DBSessionDep) -> TeamsSyncResult:
    return await TeamsIngestionService(db).sync_teams(user)
