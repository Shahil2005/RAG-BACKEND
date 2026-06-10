"""Audit persistence.

ORM port of:
  - apps/api/src/common/audit.service.ts        (AuditService.logAudit)
  - apps/api/src/audit/audit.controller.ts       (the `logs` query)

Mirrors the NestJS raw-SQL behaviour against the existing `audit_logs` table.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.schema.auth import AuthContext


class AuditService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log_audit(
        self,
        ctx: AuthContext,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Insert an audit entry (port of AuditService.logAudit)."""
        entry = AuditLog(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_=metadata or {},
        )
        self.db.add(entry)
        await self.db.commit()

    async def list_logs(self, organization_id: str, limit: int = 100) -> list[AuditLog]:
        """Most-recent audit logs for an organization (port of AuditController.logs)."""
        result = await self.db.execute(
            select(AuditLog)
            .where(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
