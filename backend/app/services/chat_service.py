"""Chat orchestration (ORM port of the NestJS ChatService).

Mirrors apps/api/src/chat/chat.service.ts. Chat sessions and messages are stored
via the SQLAlchemy ORM (ChatSession / ChatMessage). Answers come from the query
orchestration module; before answering, the original kicks off background Outlook /
document ingestion the first time a workspace has no indexed mail/files.

Sibling modules (query, ingestion, rag) form an import cycle with this one, so every
cross-service call uses a LAZY import inside the method (never at module top-level).

NOTE: the query / ingestion / rag modules are not migrated yet. Their call sites are
guarded stubs marked with TODO(migration) — they degrade gracefully so the chat CRUD
routes keep working and the backend still imports.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import logger
from app.models.chat import ChatMessage, ChatSession
from app.schema.auth import AuthContext
from app.schema.query import OrchestratedQueryOptions

# Titles the user never explicitly chose — safe to auto-rename from the first message.
_AUTO_TITLES = ("New chat", "New conversation", "Main")
_WORD_SPLIT = re.compile(r"(\s+)")


class ChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    async def create_session(
        self,
        ctx: AuthContext,
        title: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if project_id:
            await self._assert_project_owned(ctx, project_id)

        session = ChatSession(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            workspace_id=workspace_id,
            project_id=project_id,
            title=title or "New chat",
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return self._serialize_session(session)

    async def list_sessions(
        self, ctx: AuthContext, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        if project_id:
            await self._assert_project_owned(ctx, project_id)

        query = select(ChatSession).where(
            ChatSession.user_id == ctx.user_id,
            ChatSession.organization_id == ctx.organization_id,
        )
        if project_id:
            query = query.where(ChatSession.project_id == project_id)
        else:
            query = query.where(ChatSession.project_id.is_(None))
        query = query.order_by(ChatSession.updated_at.desc())

        rows = (await self.db.execute(query)).scalars().all()
        return [self._serialize_session_summary(row) for row in rows]

    async def delete_session(self, ctx: AuthContext, session_id: str) -> dict[str, bool]:
        await self._assert_session_access(ctx, session_id)
        # chat_messages cascade-delete via the session_id FK.
        await self.db.execute(
            ChatSession.__table__.delete().where(
                ChatSession.id == session_id,
                ChatSession.user_id == ctx.user_id,
                ChatSession.organization_id == ctx.organization_id,
            )
        )
        await self.db.commit()
        return {"ok": True}

    async def get_messages(
        self, ctx: AuthContext, session_id: str
    ) -> list[dict[str, Any]]:
        await self._assert_session_access(ctx, session_id)
        rows = (
            await self.db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
            )
        ).scalars().all()
        return [self._serialize_message(row) for row in rows]

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------
    async def send_message(
        self,
        ctx: AuthContext,
        session_id: str,
        content: str,
        workspace_id: str | None = None,
        sector_id: str | None = None,
    ) -> dict[str, Any]:
        await self._assert_session_access(ctx, session_id)

        logger.info(
            f"[chat] user message sessionId={session_id} "
            f"workspaceId={workspace_id or 'none'}: {content}"
        )

        await self._insert_message(session_id, "user", content)
        await self._maybe_retitle(session_id, content)
        await self._maybe_kick_off_background_sync(ctx)

        ws_id = await self._resolve_workspace_id(ctx, session_id, workspace_id)
        proj_id = await self._resolve_project_id(ctx, session_id, None)
        messages = await self.get_messages(ctx, session_id)
        history = messages[:-1] if len(messages) > 1 else []
        response = await self._run_query(
            ctx,
            content,
            workspace_id=ws_id,
            project_id=proj_id,
            sector_id=sector_id if proj_id else None,
            chat_history=history,
        )

        citations = response.get("citations", []) or []
        logger.info(
            f"[chat] assistant reply sessionId={session_id} citations={len(citations)}"
        )

        await self._insert_message(
            session_id, "assistant", response.get("answer", ""), citations=citations
        )
        await self._touch_session(session_id)
        return response

    async def stream_message(
        self,
        ctx: AuthContext,
        session_id: str,
        content: str,
        workspace_id: str | None = None,
        sector_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        await self._assert_session_access(ctx, session_id)

        await self._insert_message(session_id, "user", content)

        ws_id = await self._resolve_workspace_id(ctx, session_id, workspace_id)
        proj_id = await self._resolve_project_id(ctx, session_id, None)
        messages = await self.get_messages(ctx, session_id)
        history = messages[:-1] if len(messages) > 1 else []
        response = await self._run_query(
            ctx,
            content,
            workspace_id=ws_id,
            project_id=proj_id,
            sector_id=sector_id if proj_id else None,
            chat_history=history,
        )

        answer = response.get("answer", "") or ""
        assembled = ""
        for part in _WORD_SPLIT.split(answer):
            if part == "":
                continue
            assembled += part
            yield {"type": "token", "data": {"text": part}}
            await asyncio.sleep(0.008)

        citations = response.get("citations", []) or []
        await self._insert_message(
            session_id, "assistant", answer, citations=citations
        )
        await self._touch_session(session_id)

        yield {
            "type": "done",
            "data": {
                "answer": answer,
                "citations": citations,
                "externalResults": response.get("externalResults"),
                "intent": response.get("intent"),
                "usedExternalSearch": response.get("usedExternalSearch"),
                "emptyReason": response.get("emptyReason"),
                "scopeReason": response.get("scopeReason"),
                "documentDraft": response.get("documentDraft"),
            },
        }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    async def _insert_message(
        self,
        session_id: str,
        role: str,
        content: str,
        citations: list[Any] | None = None,
    ) -> None:
        message = ChatMessage(session_id=session_id, role=role, content=content)
        if citations is not None:
            message.citations = citations
        self.db.add(message)
        await self.db.commit()

    async def _maybe_retitle(self, session_id: str, content: str) -> None:
        """Replace a still-default title with the first user message (first 80 chars)."""
        new_title = content[:80].strip() or "New chat"
        await self.db.execute(
            update(ChatSession)
            .where(
                ChatSession.id == session_id,
                (ChatSession.title.is_(None)) | (ChatSession.title.in_(_AUTO_TITLES)),
            )
            .values(title=new_title, updated_at=text("NOW()"))
        )
        await self.db.commit()

    async def _touch_session(self, session_id: str) -> None:
        await self.db.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(updated_at=text("NOW()"))
        )
        await self.db.commit()

    # ------------------------------------------------------------------
    # Scope resolution / access control
    # ------------------------------------------------------------------
    async def _resolve_workspace_id(
        self, ctx: AuthContext, session_id: str, workspace_id: str | None
    ) -> str | None:
        if workspace_id:
            return workspace_id
        row = (
            await self.db.execute(
                select(ChatSession.workspace_id).where(
                    ChatSession.id == session_id,
                    ChatSession.user_id == ctx.user_id,
                    ChatSession.organization_id == ctx.organization_id,
                )
            )
        ).scalar_one_or_none()
        return str(row) if row else None

    async def _resolve_project_id(
        self, ctx: AuthContext, session_id: str, project_id: str | None
    ) -> str | None:
        if project_id:
            await self._assert_project_owned(ctx, project_id)
            return project_id
        row = (
            await self.db.execute(
                select(ChatSession.project_id).where(
                    ChatSession.id == session_id,
                    ChatSession.user_id == ctx.user_id,
                    ChatSession.organization_id == ctx.organization_id,
                )
            )
        ).scalar_one_or_none()
        return str(row) if row else None

    async def _assert_project_owned(self, ctx: AuthContext, project_id: str) -> None:
        """Projects is owned by the (not-yet-ported) projects module; query it by name.

        Done via a raw textual query so we don't need to import/redefine the projects
        ORM model here.
        """
        row = (
            await self.db.execute(
                text(
                    "SELECT id FROM projects "
                    "WHERE id = :id AND organization_id = :org AND user_id = :user"
                ),
                {
                    "id": project_id,
                    "org": ctx.organization_id,
                    "user": ctx.user_id,
                },
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )

    async def _assert_session_access(self, ctx: AuthContext, session_id: str) -> None:
        row = (
            await self.db.execute(
                select(ChatSession.id).where(
                    ChatSession.id == session_id,
                    ChatSession.user_id == ctx.user_id,
                    ChatSession.organization_id == ctx.organization_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
            )

    # ------------------------------------------------------------------
    # Cross-service calls (LAZY imports — query / ingestion / rag form a cycle)
    # ------------------------------------------------------------------
    async def _run_query(
        self,
        ctx: AuthContext,
        content: str,
        workspace_id: str | None,
        project_id: str | None,
        sector_id: str | None,
        chat_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate to the query orchestration module to produce the assistant answer."""
        try:
            # Lazy import to avoid the rag/ingestion/query <-> chat import cycle.
            from app.services.query_service import QueryOrchestrationService
        except ImportError:
            # TODO(migration): wire to the query module once it is ported. Until then
            # return an empty-but-valid RagQueryResponse-shaped payload so chat works.
            logger.warning(
                "[chat] query module not migrated yet; returning empty answer"
            )
            return {
                "answer": "",
                "citations": [],
                "chunks": [],
                "externalResults": None,
                "intent": None,
                "usedExternalSearch": None,
                "scopeReason": None,
                "emptyReason": None,
                "documentDraft": None,
            }

        orchestrator = QueryOrchestrationService(self.db)
        response = await orchestrator.query(
            ctx,
            content,
            OrchestratedQueryOptions(
                workspace_id=workspace_id,
                project_id=project_id,
                sector_id=sector_id,
                chat_history=chat_history,
            ),
        )
        if hasattr(response, "model_dump"):
            return response.model_dump(by_alias=True)
        return dict(response)

    async def _maybe_kick_off_background_sync(self, ctx: AuthContext) -> None:
        """First-touch ingestion: if a workspace has no mail/files, sync in the background.

        Mirrors ChatService.sendMessage's fire-and-forget Outlook + documents sync. Never
        blocks the reply; failures are logged and swallowed.
        """
        try:
            # Lazy imports to avoid the rag/ingestion <-> chat import cycle.
            from app.services.ingestion_service import IngestionService
            from app.services.outlook_mail_service import OutlookMailService
            from app.services.sharepoint_documents_service import SharePointDocumentsService
        except ImportError:
            # TODO(migration): wire to ingestion / rag modules once they are ported.
            # Until then there is nothing to sync; skip silently (CRUD still works).
            return

        ingestion = IngestionService(self.db)

        try:
            mail_count = await OutlookMailService(self.db).count_email_metadata(ctx)
            if mail_count == 0:
                asyncio.create_task(self._safe_sync(ingestion.sync_outlook(ctx), "outlook"))
        except Exception as err:
            logger.warning(f"[chat] outlook count failed: {err}")

        try:
            file_count = await SharePointDocumentsService(self.db).count_file_metadata(ctx)
            if file_count == 0:
                asyncio.create_task(
                    self._safe_sync(ingestion.sync_all_documents(ctx), "documents")
                )
        except Exception as err:
            logger.warning(f"[chat] documents count failed: {err}")

    @staticmethod
    async def _safe_sync(coro: Any, label: str) -> None:
        try:
            await coro
        except Exception as err:
            logger.warning(f"[chat] background {label} sync failed: {err}")

    # ------------------------------------------------------------------
    # Serialization (preserve the raw-pg-row snake_case JSON shape)
    # ------------------------------------------------------------------
    @staticmethod
    def _serialize_session(row: ChatSession) -> dict[str, Any]:
        """RETURNING * from the INSERT — all columns, snake_case keys."""
        return {
            "id": str(row.id),
            "organization_id": str(row.organization_id),
            "workspace_id": str(row.workspace_id) if row.workspace_id else None,
            "user_id": str(row.user_id),
            "project_id": str(row.project_id) if row.project_id else None,
            "title": row.title,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _serialize_session_summary(row: ChatSession) -> dict[str, Any]:
        """Projection used by listSessions (id, title, workspace_id, project_id, ...)."""
        return {
            "id": str(row.id),
            "title": row.title,
            "workspace_id": str(row.workspace_id) if row.workspace_id else None,
            "project_id": str(row.project_id) if row.project_id else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _serialize_message(row: ChatMessage) -> dict[str, Any]:
        citations = row.citations
        if isinstance(citations, str):
            try:
                citations = json.loads(citations)
            except (ValueError, TypeError):
                citations = []
        return {
            "id": str(row.id),
            "session_id": str(row.session_id),
            "role": row.role,
            "content": row.content,
            "citations": citations,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
