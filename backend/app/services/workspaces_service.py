"""Workspace persistence (ORM port of the NestJS WorkspacesService).

Translates the raw `pg` queries in apps/api/src/workspaces/workspaces.service.ts to
SQLAlchemy ORM. Cross-service calls (AuditService) use a lazy import inside the method
to avoid circular-import issues at boot.
"""

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import workspace_partition
from app.models.workspaces import Workspace, WorkspaceInstruction
from app.schema.auth import AuthContext

DEFAULT_WORKSPACES = [
    {"name": "Sales", "slug": "sales", "description": "Sales pipeline and customer context"},
    {"name": "Operations", "slug": "operations", "description": "Operational workflows"},
    {
        "name": "Restoration",
        "slug": "restoration",
        "description": "Restoration project knowledge",
    },
]


class WorkspacesService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ensure_defaults(self, ctx: AuthContext) -> None:
        """Upsert the default workspaces for the caller's organization."""
        for ws in DEFAULT_WORKSPACES:
            stmt = pg_insert(Workspace).values(
                organization_id=ctx.organization_id,
                name=ws["name"],
                slug=ws["slug"],
                description=ws["description"],
                pinecone_partition=workspace_partition(ws["slug"]),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["organization_id", "slug"],
                set_={
                    "name": stmt.excluded.name,
                    "description": stmt.excluded.description,
                    "updated_at": func.now(),
                },
            )
            await self.db.execute(stmt)
        await self.db.commit()

    async def list(self, ctx: AuthContext) -> list[dict]:
        """Workspaces for the org, each with its active instructions (port of `list`)."""
        await self.ensure_defaults(ctx)

        result = await self.db.execute(
            select(Workspace).where(Workspace.organization_id == ctx.organization_id)
        )
        workspaces = list(result.scalars().all())

        # Active instructions per workspace (LEFT JOIN ... AND wi.is_active = true).
        active = await self.db.execute(
            select(WorkspaceInstruction).where(
                WorkspaceInstruction.workspace_id.in_([w.id for w in workspaces]),
                WorkspaceInstruction.is_active.is_(True),
            )
        )
        by_workspace: dict[str, list[dict]] = {}
        for wi in active.scalars().all():
            by_workspace.setdefault(str(wi.workspace_id), []).append(
                {
                    "instructions": wi.instructions,
                    "version": wi.version,
                    "is_active": wi.is_active,
                }
            )

        rows: list[dict] = []
        for w in workspaces:
            rows.append(
                {
                    "id": str(w.id),
                    "organization_id": str(w.organization_id),
                    "name": w.name,
                    "slug": w.slug,
                    "description": w.description,
                    "pinecone_partition": w.pinecone_partition,
                    "created_at": w.created_at,
                    "updated_at": w.updated_at,
                    "workspace_instructions": by_workspace.get(str(w.id), []),
                }
            )
        return rows

    async def update_instructions(
        self,
        ctx: AuthContext,
        workspace_id: str,
        instructions: str,
    ) -> dict:
        """Version + activate a new instruction set (port of `updateInstructions`)."""
        current = (
            await self.db.execute(
                select(WorkspaceInstruction.version)
                .where(WorkspaceInstruction.workspace_id == workspace_id)
                .order_by(WorkspaceInstruction.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        version = (current or 0) + 1

        await self.db.execute(
            update(WorkspaceInstruction)
            .where(WorkspaceInstruction.workspace_id == workspace_id)
            .values(is_active=False)
        )

        row = (
            await self.db.execute(
                pg_insert(WorkspaceInstruction)
                .values(
                    workspace_id=workspace_id,
                    instructions=instructions,
                    version=version,
                    is_active=True,
                )
                .returning(WorkspaceInstruction)
            )
        ).scalar_one_or_none()

        if row is None:
            msg = "Failed to update instructions"
            raise RuntimeError(msg)

        await self.db.commit()

        # Cross-service call: lazy import to avoid circular imports at boot.
        from app.services.audit_service import AuditService

        await AuditService(self.db).log_audit(
            ctx, "workspace.instructions.update", "workspace", workspace_id
        )

        return {
            "id": str(row.id),
            "workspace_id": str(row.workspace_id),
            "instructions": row.instructions,
            "version": row.version,
            "is_active": row.is_active,
            "created_at": row.created_at,
        }
