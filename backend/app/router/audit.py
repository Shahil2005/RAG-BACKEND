"""Audit routes — port of the NestJS AuditController.

Endpoint paths and JSON shapes are preserved exactly (mounted under /api/v1 by the
parent router). The controller is protected by JwtAuthGuard + RolesGuard('admin',
'owner'); that role hierarchy check is reproduced here.
"""

from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.audit import AuditLogRow
from app.schema.auth import AuthContext
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])

# Mirrors RolesGuard's hierarchy (apps/api/src/auth/guards/roles.guard.ts).
_ROLE_HIERARCHY = ("viewer", "member", "admin", "owner")


def _require_roles(user: AuthContext, *required: str) -> None:
    """Reproduce RolesGuard: user level must meet the lowest required level."""
    user_level = _ROLE_HIERARCHY.index(user.role.value)
    min_required = min(_ROLE_HIERARCHY.index(r) for r in required)
    if user_level < min_required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )


@router.get("/logs")
async def logs(user: CurrentUserDep, db: DBSessionDep) -> list[AuditLogRow]:
    _require_roles(user, "admin", "owner")
    rows = await AuditService(db).list_logs(user.organization_id)
    return [
        AuditLogRow(
            id=str(row.id),
            organization_id=str(row.organization_id),
            user_id=str(row.user_id) if row.user_id is not None else None,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            metadata=row.metadata_,
            ip_address=row.ip_address,
            created_at=row.created_at,
        )
        for row in rows
    ]
