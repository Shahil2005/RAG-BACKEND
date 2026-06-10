"""Projects domain services (ORM port of the NestJS projects module).

Two services, mirroring the NestJS split:
  - :class:`ProjectsService`      -> projects.service.ts
  - :class:`ProjectFilesService`  -> project-files.service.ts

Sibling modules (ingestion ``document-text``, ``pinecone``, ``ai``, ``common``
audit) form an import cycle with this module, so every cross-service call uses a
LAZY import inside the method that needs it. The raw ``pg`` queries of the
originals are translated to SQLAlchemy async ORM queries against the models in
:mod:`app.models.projects`.
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import logger
from app.core.settings import settings
from app.core.utils import vector_id
from app.models.projects import Project, ProjectFile, ProjectSector
from app.schema.auth import AuthContext
from app.schema.common import ProjectDetail, ProjectFileSummary, ProjectSummary
from app.schema.common import ProjectSector as ProjectSectorSchema

_DEFAULT_MAX_BYTES = 5_242_880  # 5 MiB (matches DOCUMENT_MAX_BYTES default)


def _iso(value: datetime | None) -> str | None:
    """Serialize a DB ``datetime`` to ISO-8601 (None-safe)."""
    if value is None:
        return None
    return value.isoformat()


class ProjectsService:
    """CRUD + sector management for a user's projects.

    Ownership is enforced exactly like the original: a project must match both
    ``organization_id`` and ``user_id`` from the request's :class:`AuthContext`.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- helpers ---------------------------------------------------------

    def _project_summary(self, row: Project) -> ProjectSummary:
        return ProjectSummary(
            id=str(row.id),
            name=row.name,
            description=row.description,
            custom_instructions=row.custom_instructions,
            created_at=_iso(row.created_at),
            updated_at=_iso(row.updated_at),
        )

    def _sector_schema(self, row: ProjectSector) -> ProjectSectorSchema:
        return ProjectSectorSchema(
            id=str(row.id),
            project_id=str(row.project_id),
            name=row.name,
            created_at=_iso(row.created_at),
        )

    async def _log_audit(
        self,
        ctx: AuthContext,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Write an audit row via the common audit service (lazy import).

        Degrades to a no-op if the ``common`` module has not been ported yet,
        so projects keep working before the audit slice lands.
        """
        try:
            from app.services.audit_service import AuditService
        except ImportError:
            # TODO(migration): wire to the common/audit module once it is ported.
            return
        await AuditService(self.db).log_audit(ctx, action, resource_type, resource_id)

    # --- projects --------------------------------------------------------

    async def list(self, ctx: AuthContext) -> list[ProjectSummary]:
        result = await self.db.execute(
            select(Project)
            .where(
                Project.organization_id == ctx.organization_id,
                Project.user_id == ctx.user_id,
            )
            .order_by(Project.updated_at.desc())
        )
        return [self._project_summary(row) for row in result.scalars().all()]

    async def create(self, ctx: AuthContext, input: Any) -> ProjectSummary:
        row = Project(
            organization_id=uuid.UUID(ctx.organization_id),
            user_id=uuid.UUID(ctx.user_id),
            name=input.name.strip(),
            description=(input.description.strip() if input.description else None),
            custom_instructions=(
                input.custom_instructions.strip() if input.custom_instructions else None
            ),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        await self._log_audit(ctx, "project.create", "project", str(row.id))
        return self._project_summary(row)

    async def get_by_id(self, ctx: AuthContext, project_id: str) -> ProjectDetail:
        project = await self.require_owned(ctx, project_id)
        from app.services.projects_service import ProjectFilesService

        files = await ProjectFilesService(self.db).list_files(ctx, project_id)
        sectors = await self.list_sectors(ctx, project_id)
        return ProjectDetail(
            **project.model_dump(by_alias=False),
            files=files,
            sectors=sectors,
        )

    async def list_sectors(
        self, ctx: AuthContext, project_id: str
    ) -> list[ProjectSectorSchema]:
        await self.require_owned(ctx, project_id)
        result = await self.db.execute(
            select(ProjectSector)
            .where(
                ProjectSector.project_id == project_id,
                ProjectSector.organization_id == ctx.organization_id,
            )
            .order_by(ProjectSector.name.asc())
        )
        return [self._sector_schema(row) for row in result.scalars().all()]

    async def create_sector(
        self, ctx: AuthContext, project_id: str, name: str
    ) -> ProjectSectorSchema:
        await self.require_owned(ctx, project_id)
        trimmed = name.strip()
        if not trimmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Sector name is required"
            )

        existing = (
            await self.db.execute(
                select(ProjectSector.id).where(
                    ProjectSector.project_id == project_id,
                    ProjectSector.organization_id == ctx.organization_id,
                    func.lower(ProjectSector.name) == trimmed.lower(),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A sector with that name already exists",
            )

        row = ProjectSector(
            project_id=uuid.UUID(project_id),
            organization_id=uuid.UUID(ctx.organization_id),
            name=trimmed,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        await self._log_audit(ctx, "project.sector.create", "project", project_id)
        return self._sector_schema(row)

    async def delete_sector(
        self, ctx: AuthContext, project_id: str, sector_id: str
    ) -> None:
        await self.require_owned(ctx, project_id)
        result = await self.db.execute(
            delete(ProjectSector).where(
                ProjectSector.id == sector_id,
                ProjectSector.project_id == project_id,
                ProjectSector.organization_id == ctx.organization_id,
            )
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Sector not found"
            )
        await self.db.commit()
        await self._log_audit(ctx, "project.sector.delete", "project", project_id)

    async def require_sector(
        self, ctx: AuthContext, project_id: str, sector_id: str
    ) -> str:
        """Validate that a sector belongs to the project; returns the id or 404s."""
        row = (
            await self.db.execute(
                select(ProjectSector.id).where(
                    ProjectSector.id == sector_id,
                    ProjectSector.project_id == project_id,
                    ProjectSector.organization_id == ctx.organization_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Sector not found"
            )
        return str(row)

    async def update(self, ctx: AuthContext, project_id: str, input: Any) -> ProjectSummary:
        await self.require_owned(ctx, project_id)

        fields_set = input.model_fields_set
        values: dict[str, Any] = {"updated_at": func.now()}

        # COALESCE($, col): only overwrite when a non-null trimmed value is given.
        name = input.name.strip() if input.name else None
        if name is not None:
            values["name"] = name

        if "description" in fields_set:
            description = input.description.strip() if input.description else None
            if description is not None:
                values["description"] = description

        if "custom_instructions" in fields_set:
            ci = input.custom_instructions.strip() if input.custom_instructions else None
            if ci is not None:
                values["custom_instructions"] = ci

        result = await self.db.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.organization_id == ctx.organization_id,
                Project.user_id == ctx.user_id,
            )
            .values(**values)
            .returning(Project)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        await self.db.commit()
        await self._log_audit(ctx, "project.update", "project", project_id)
        return self._project_summary(row)

    async def remove(self, ctx: AuthContext, project_id: str) -> None:
        await self.require_owned(ctx, project_id)
        from app.services.projects_service import ProjectFilesService

        await ProjectFilesService(self.db).delete_all_for_project(ctx, project_id)
        await self.db.execute(
            delete(Project).where(
                Project.id == project_id,
                Project.organization_id == ctx.organization_id,
                Project.user_id == ctx.user_id,
            )
        )
        await self.db.commit()
        await self._log_audit(ctx, "project.delete", "project", project_id)

    async def require_owned(self, ctx: AuthContext, project_id: str) -> ProjectSummary:
        row = (
            await self.db.execute(
                select(Project).where(
                    Project.id == project_id,
                    Project.organization_id == ctx.organization_id,
                    Project.user_id == ctx.user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        return self._project_summary(row)

    async def get_instructions_for_rag(
        self, ctx: AuthContext, project_id: str
    ) -> dict[str, str | None]:
        row = (
            await self.db.execute(
                select(Project.custom_instructions, Project.description).where(
                    Project.id == project_id,
                    Project.organization_id == ctx.organization_id,
                    Project.user_id == ctx.user_id,
                )
            )
        ).first()
        if row is None:
            return {}
        parts: list[str] = []
        description = row.description
        custom_instructions = row.custom_instructions
        if description and description.strip():
            parts.append(f"Project description: {description.strip()}")
        if custom_instructions and custom_instructions.strip():
            parts.append(custom_instructions.strip())
        return {
            "instructions": "\n\n".join(parts) if parts else None,
            "description": description or None,
        }


class ProjectFilesService:
    """Upload, index, re-sector and delete a project's knowledge-base files.

    File extraction (``ingestion.document_text``), embeddings (``ai``) and the
    vector store (``pinecone``) are reached via LAZY imports to avoid the
    rag/ingestion/projects import cycle.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.upload_dir = getattr(
            settings, "project_upload_dir", None
        ) or "./data/project-uploads"
        self.max_bytes = int(
            getattr(settings, "document_max_bytes", None) or _DEFAULT_MAX_BYTES
        )

    # --- helpers ---------------------------------------------------------

    def _projects(self) -> ProjectsService:
        return ProjectsService(self.db)

    def _file_summary(self, row: ProjectFile) -> ProjectFileSummary:
        return ProjectFileSummary(
            id=str(row.id),
            project_id=str(row.project_id),
            file_name=row.file_name,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            is_indexed=row.is_indexed,
            indexed_at=_iso(row.indexed_at),
            index_reason=row.index_reason,
            chunk_count=row.chunk_count,
            sector_id=str(row.sector_id) if row.sector_id else None,
            created_at=_iso(row.created_at),
        )

    async def _get_file(self, file_id: str) -> ProjectFile | None:
        return (
            await self.db.execute(select(ProjectFile).where(ProjectFile.id == file_id))
        ).scalar_one_or_none()

    async def _get_file_row(self, file_id: str) -> ProjectFileSummary:
        row = await self._get_file(file_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project file not found"
            )
        return self._file_summary(row)

    def ensure_upload_dir(self) -> None:
        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)

    # --- listing ---------------------------------------------------------

    async def list_files(
        self, ctx: AuthContext, project_id: str
    ) -> list[ProjectFileSummary]:
        await self._projects().require_owned(ctx, project_id)
        result = await self.db.execute(
            select(ProjectFile)
            .where(
                ProjectFile.project_id == project_id,
                ProjectFile.organization_id == ctx.organization_id,
            )
            .order_by(ProjectFile.created_at.desc())
        )
        return [self._file_summary(row) for row in result.scalars().all()]

    # --- upload + index --------------------------------------------------

    async def upload_and_index(
        self,
        ctx: AuthContext,
        project_id: str,
        file_buffer: bytes,
        original_name: str | None,
        mime_type: str | None,
        size: int | None = None,
        sector_id: str | None = None,
    ) -> ProjectFileSummary:
        await self._projects().require_owned(ctx, project_id)
        if not file_buffer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No file uploaded"
            )
        if sector_id:
            await self._projects().require_sector(ctx, project_id, sector_id)

        resolved_size = size if size is not None else len(file_buffer)
        if resolved_size > self.max_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File exceeds maximum size of {self.max_bytes} bytes",
            )

        self.ensure_upload_dir()
        file_id = str(uuid.uuid4())
        safe_name = os.path.basename(original_name or "upload.bin")
        rel_dir = os.path.join(ctx.organization_id, project_id)
        abs_dir = os.path.join(self.upload_dir, rel_dir)
        Path(abs_dir).mkdir(parents=True, exist_ok=True)
        storage_path = os.path.join(rel_dir, f"{file_id}-{safe_name}")
        abs_path = os.path.join(self.upload_dir, storage_path)
        with open(abs_path, "wb") as fh:
            fh.write(file_buffer)

        row = ProjectFile(
            id=uuid.UUID(file_id),
            project_id=uuid.UUID(project_id),
            organization_id=uuid.UUID(ctx.organization_id),
            file_name=safe_name,
            storage_path=storage_path,
            mime_type=mime_type,
            size_bytes=resolved_size,
            sector_id=uuid.UUID(sector_id) if sector_id else None,
        )
        self.db.add(row)
        await self.db.commit()

        try:
            return await self.index_file(ctx, project_id, file_id)
        except Exception as err:
            logger.warning(f"[project-files] index failed fileId={file_id}: {err}")
            return await self._get_file_row(file_id)

    async def index_file(
        self, ctx: AuthContext, project_id: str, file_id: str
    ) -> ProjectFileSummary:
        await self._projects().require_owned(ctx, project_id)

        meta = (
            await self.db.execute(
                select(ProjectFile).where(
                    ProjectFile.id == file_id,
                    ProjectFile.project_id == project_id,
                    ProjectFile.organization_id == ctx.organization_id,
                )
            )
        ).scalar_one_or_none()
        if meta is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project file not found"
            )

        # Lazy sibling imports (ingestion / ai / pinecone) — avoid the cycle.
        from app.services.ai_client import AiClientService
        from app.services.document_text_service import DocumentTextService
        from app.services.pinecone_service import PineconeService

        document_text = DocumentTextService()
        ai = AiClientService()
        pinecone = PineconeService()

        abs_path = os.path.join(self.upload_dir, meta.storage_path)
        with open(abs_path, "rb") as fh:
            buffer = fh.read()

        text, skip_reason = await document_text.extract_from_buffer(
            meta.file_name, buffer, meta.mime_type
        )
        extract_result = document_text.resolve_chunks(
            meta.file_name, None, text, skip_reason
        )
        chunks: list[str] = extract_result["chunks"]
        reason: str = extract_result["reason"]

        if meta.chunk_count > 0:
            await self._delete_vectors_for_file(
                str(meta.id), meta.chunk_count, ctx.organization_id, pinecone
            )

        if not chunks:
            await self.db.execute(
                update(ProjectFile)
                .where(ProjectFile.id == file_id)
                .values(
                    is_indexed=False,
                    index_reason=reason,
                    chunk_count=0,
                    indexed_at=None,
                )
            )
            await self.db.commit()
            return await self._get_file_row(file_id)

        content_id = str(meta.id)
        stable_ids = [vector_id("project", content_id, str(i)) for i in range(len(chunks))]
        await pinecone.delete_by_vector_ids(ctx.organization_id, stable_ids)

        embed_result = await ai.embed(chunks)
        embeddings = embed_result["embeddings"]
        sector_meta = {"sectorId": str(meta.sector_id)} if meta.sector_id else {}
        vectors = [
            pinecone.build_upsert(
                "project",
                content_id,
                str(i),
                embeddings[i],
                {
                    "source": "project",
                    "organizationId": ctx.organization_id,
                    "projectId": project_id,
                    **sector_meta,
                    "fileId": content_id,
                    "fileName": meta.file_name,
                    "text": chunk_text,
                },
            )
            for i, chunk_text in enumerate(chunks)
        ]
        await pinecone.upsert(ctx.organization_id, vectors)

        await self.db.execute(
            update(ProjectFile)
            .where(ProjectFile.id == file_id)
            .values(
                is_indexed=True,
                index_reason=reason,
                chunk_count=len(chunks),
                indexed_at=func.now(),
            )
        )
        await self.db.execute(
            update(Project).where(Project.id == project_id).values(updated_at=func.now())
        )
        await self.db.commit()

        return await self._get_file_row(file_id)

    async def assign_sector(
        self,
        ctx: AuthContext,
        project_id: str,
        file_id: str,
        sector_id: str | None,
    ) -> ProjectFileSummary:
        await self._projects().require_owned(ctx, project_id)
        if sector_id:
            await self._projects().require_sector(ctx, project_id, sector_id)

        result = await self.db.execute(
            update(ProjectFile)
            .where(
                ProjectFile.id == file_id,
                ProjectFile.project_id == project_id,
                ProjectFile.organization_id == ctx.organization_id,
            )
            .values(sector_id=uuid.UUID(sector_id) if sector_id else None)
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project file not found"
            )
        await self.db.commit()

        # Re-index so the file's vectors carry the new sectorId in their metadata.
        try:
            return await self.index_file(ctx, project_id, file_id)
        except Exception as err:
            logger.warning(
                f"[project-files] reindex after sector change failed fileId={file_id}: {err}"
            )
            return await self._get_file_row(file_id)

    async def delete_file(
        self, ctx: AuthContext, project_id: str, file_id: str
    ) -> None:
        await self._projects().require_owned(ctx, project_id)

        meta = (
            await self.db.execute(
                select(ProjectFile).where(
                    ProjectFile.id == file_id,
                    ProjectFile.project_id == project_id,
                    ProjectFile.organization_id == ctx.organization_id,
                )
            )
        ).scalar_one_or_none()
        if meta is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project file not found"
            )

        if meta.chunk_count > 0:
            from app.services.pinecone_service import PineconeService

            await self._delete_vectors_for_file(
                str(meta.id), meta.chunk_count, ctx.organization_id, PineconeService()
            )

        abs_path = os.path.join(self.upload_dir, meta.storage_path)
        try:
            os.unlink(abs_path)
        except OSError:
            pass

        await self.db.execute(delete(ProjectFile).where(ProjectFile.id == file_id))
        await self.db.commit()

    async def delete_all_for_project(
        self, ctx: AuthContext, project_id: str
    ) -> None:
        files = (
            await self.db.execute(
                select(ProjectFile).where(ProjectFile.project_id == project_id)
            )
        ).scalars().all()

        pinecone = None
        for f in files:
            if f.chunk_count > 0:
                if pinecone is None:
                    from app.services.pinecone_service import PineconeService

                    pinecone = PineconeService()
                await self._delete_vectors_for_file(
                    str(f.id), f.chunk_count, ctx.organization_id, pinecone
                )
            abs_path = os.path.join(self.upload_dir, f.storage_path)
            try:
                os.unlink(abs_path)
            except OSError:
                pass

        await self.db.execute(
            delete(ProjectFile).where(ProjectFile.project_id == project_id)
        )
        await self.db.commit()
        project_dir = os.path.join(self.upload_dir, ctx.organization_id, project_id)
        shutil.rmtree(project_dir, ignore_errors=True)

    async def _delete_vectors_for_file(
        self,
        file_id: str,
        chunk_count: int,
        organization_id: str,
        pinecone: Any,
    ) -> None:
        ids = [vector_id("project", file_id, str(i)) for i in range(chunk_count)]
        await pinecone.delete_by_vector_ids(organization_id, ids)
