"""Teams ingestion service (ORM port of the NestJS TeamsIngestionService).

v1 placeholder: Microsoft Teams channel indexing is not implemented yet. The
syncTeams method records an audit-log entry and returns guidance, exactly mirroring
the original NestJS behaviour, until the `ChannelMessage.Read.All` Graph scope is
granted and the channel crawl is built.
"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import logger
from app.core.settings import settings
from app.schema.auth import AuthContext
from app.schema.teams import TeamsSyncResult

_TEAMS_ENABLED_MESSAGE = (
    "Teams channel indexing is not yet enabled in this build. Grant "
    "ChannelMessage.Read.All and implement Graph channel crawl."
)
_TEAMS_DISABLED_MESSAGE = (
    "Teams ingestion is disabled. Set ENABLE_TEAMS_INGESTION=true after admin "
    "consent for Teams scopes."
)


class TeamsIngestionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def sync_teams(self, ctx: AuthContext) -> TeamsSyncResult:
        """v1 placeholder: logs audit and returns guidance until the Teams scope is granted."""
        enabled = settings.enable_teams_ingestion
        result = TeamsSyncResult(
            indexed=0,
            skipped=0,
            message=_TEAMS_ENABLED_MESSAGE if enabled else _TEAMS_DISABLED_MESSAGE,
        )

        await self._log_audit(
            ctx,
            "ingestion.teams.sync",
            "ingestion",
            None,
            result.model_dump(),
        )
        logger.info(f"[teams] sync placeholder org={ctx.organization_id}")
        return result

    async def _log_audit(
        self,
        ctx: AuthContext,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Insert an audit_logs row.

        Mirrors the raw pg INSERT in the original common/AuditService.logAudit.
        TODO(migration): swap this for a lazy import of the shared AuditService once
        the audit/common module is ported (audit_logs is owned by that module, not
        teams), e.g. `from app.services.audit_service import AuditService`.
        """
        await self.db.execute(
            text(
                "INSERT INTO audit_logs "
                "(organization_id, user_id, action, resource_type, resource_id, metadata) "
                "VALUES (:organization_id, :user_id, :action, :resource_type, "
                ":resource_id, CAST(:metadata AS JSONB))"
            ),
            {
                "organization_id": ctx.organization_id,
                "user_id": ctx.user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "metadata": json.dumps(metadata or {}),
            },
        )
        await self.db.commit()
