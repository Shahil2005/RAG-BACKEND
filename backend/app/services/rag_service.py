"""RAG orchestration (port of apps/api/src/rag/*.service.ts).

Ports the whole NestJS ``rag`` module into one service module:

  - :class:`RagService`              (rag.service.ts)
  - :class:`PermissionService`       (permission.service.ts)
  - :class:`HybridRetrievalService`  (hybrid-retrieval.service.ts)
  - :class:`OutlookMailService`      (outlook-mail.service.ts)
  - :class:`SharePointDocumentsService` (sharepoint-documents.service.ts)

plus the pure query-classification helpers from ``document-query.util.ts``,
``mail-query.util.ts`` and the ``topic-guard`` re-exports of
``query/query-intent.util.ts`` (the ``query`` module is not migrated yet, so the
helpers it shares with RAG are inlined here).

Cross-module siblings (ai, graph, ingestion, pinecone, projects, redis) form an
import cycle, so they are imported LAZILY inside the methods that use them.
Several of those sibling modules are not migrated yet; calls to them are wrapped
so a missing module degrades gracefully (empty retrieval / no live context)
instead of breaking import or the request. Such gaps are marked
``TODO(migration)``.
"""

import base64
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import logger
from app.core.constants import DEFAULT_TOP_K, LLM_MODEL, RERANK_TOP_K
from app.core.utils import normalize_keys, reciprocal_rank_fusion, vector_id
from app.models.rag import EmailMetadata, FileMetadata, Project, ProjectFile, WorkspaceInstructions
from app.prompts import RAG_SYSTEM_PROMPT, build_rag_user_prompt
from app.schema.auth import AuthContext
from app.schema.common import Citation, RagQueryResponse, RetrievedChunk, VectorMetadata, VectorSource
from app.schema.rag import LiveContextBlock, LiveContextResult, RagQueryOptions
from app.services.ai_client import AiClientService
from app.services.redis_service import RedisService


def _coerce_rag_options(
    options: "RagQueryOptions | dict | None",
) -> RagQueryOptions:
    """Accept a RagQueryOptions, a (camelCase/snake_case) dict, or None.

    Sibling callers (query/documents/chat) pass option dicts with mixed key casing;
    normalize them onto the RagQueryOptions dataclass fields.
    """
    if options is None:
        return RagQueryOptions()
    if isinstance(options, dict):
        valid = set(RagQueryOptions.__dataclass_fields__)
        return RagQueryOptions(
            **{k: v for k, v in normalize_keys(options).items() if k in valid}
        )
    return options


EMPTY_MAILBOX_MESSAGE = (
    "Your Microsoft mailbox is connected, but no recent messages were returned. "
    "Try signing in again or check connection in Settings."
)

EMPTY_DOCUMENTS_MESSAGE = (
    "Microsoft 365 is connected, but no matching documents were found. Open "
    "Settings and run document sync, or try again after indexing completes."
)


# ---------------------------------------------------------------------------
# Query-classification helpers (document-query.util.ts, mail-query.util.ts,
# query/query-intent.util.ts). Pure functions; inlined because the `query`
# module they live in is not migrated yet.
# ---------------------------------------------------------------------------

_DOCUMENT_RECENCY_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(list|show|give|fetch|get)\s+(me\s+)?(my\s+)?(recent\s+)?(files?|documents?)",
        r"\bwhat\s+(are\s+)?my\s+(recent\s+)?(files?|documents?)",
        r"\b(recent|latest|newest)\s+(files?|documents?|sharepoint|onedrive)",
        r"\b(files?|documents?)\s+(in|on|from)\s+(my\s+)?(sharepoint|onedrive)",
        r"\bmy\s+(sharepoint|onedrive)\s+(files?|documents?)",
        r"\bsharepoint\s+(files?|documents?)\b",
        r"\bonedrive\s+(files?|documents?)\b",
    )
]

_DOCUMENT_CONTENT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(document|file|sharepoint|onedrive)\b",
        r"\b(contract|invoice|renewal|msa|pdf|docx|csv)\b",
        r"\bwhat\s+(does|is|says?)\b",
        r"\bsummarize\b",
        r"\bexplain\b",
    )
]

_MAIL_RECENCY_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brecent\b",
        r"\blatest\b",
        r"\bnewest\b",
        r"\bunread\b",
        r"\binbox\b",
        r"\boutlook\s+mail",
        r"\bread\s+(my\s+)?(outlook\s+)?(mail|email)",
        r"\b(list|show|give|fetch|get)\s+(me\s+)?(my\s+)?(recent\s+)?(emails?|messages?|mails?)",
        r"\b(emails?|messages?|mails?)\s+(from|in)\s+(my\s+)?(outlook|mailbox)",
        r"\bwhat\s+(are\s+)?my\s+(recent\s+)?(emails?|messages?)",
    )
]


def is_document_recency_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    return any(p.search(normalized) for p in _DOCUMENT_RECENCY_PATTERNS)


def is_document_content_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    return any(p.search(normalized) for p in _DOCUMENT_CONTENT_PATTERNS)


def is_mail_recency_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    return any(p.search(normalized) for p in _MAIL_RECENCY_PATTERNS)


OUT_OF_SCOPE_REFUSAL_MESSAGE = (
    "I can help with AppXcess Technologies work: your Microsoft 365 mail and "
    "documents, plus business research such as industry benchmarks, vendor or "
    "company lookup, and pricing references. Personal topics like weather or "
    "sports are not supported."
)

_OFF_TOPIC_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bweather\b",
        r"\bforecast\b",
        r"\b(sports?|cricket|football|nba|nfl)\b",
        r"\b(recipe|cook|restaurant)\b",
        r"\b(latest\s+news|breaking\s+news|headlines)\b",
        r"\bwho\s+(is|was|won)\s+the\s+(president|election|match|game)\b",
        r"\b(write|generate)\s+(me\s+)?(python|javascript|java|code)\b",
        r"\b(movie|netflix|song|lyrics)\b",
        r"\b(crypto|bitcoin|stock\s+price)\b",
    )
]


def _is_off_topic_general_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    return any(p.search(normalized) for p in _OFF_TOPIC_PATTERNS)


def assert_in_scope_query(query: str) -> dict[str, Any]:
    """In scope unless the query is off-topic (port of assertInScopeQuery).

    TODO(migration): the original delegates to the full ``query`` intent
    classifier (document-generation / business-research detection). Until the
    ``query`` module is ported, this uses the off-topic gate — the practical
    behaviour for RAG topic-guarding is unchanged (off-topic still refused).
    """
    normalized = query.strip()
    if _is_off_topic_general_query(normalized):
        reason = "empty_query" if not normalized else "not_work_related"
        return {"in_scope": False, "reason": reason}
    return {"in_scope": True, "reason": None}


# ---------------------------------------------------------------------------
# PermissionService (permission.service.ts)
# ---------------------------------------------------------------------------


class PermissionService:
    """Restrict retrieved chunks to content the user is allowed to see."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_allowed_content_ids(
        self,
        ctx: AuthContext,
        sources: list[VectorSource],
        project_id: str | None = None,
        sector_id: str | None = None,
    ) -> dict[str, set[str]]:
        email_ids: set[str] = set()
        file_ids: set[str] = set()
        project_file_ids: set[str] = set()

        if any(self._src(s) == "outlook" for s in sources):
            rows = (
                await self.db.execute(
                    select(EmailMetadata.id, EmailMetadata.graph_message_id).where(
                        EmailMetadata.organization_id == ctx.organization_id,
                        EmailMetadata.user_id == ctx.user_id,
                    )
                )
            ).all()
            for row in rows:
                email_ids.add(str(row.id))
                email_ids.add(row.graph_message_id)

        if any(self._src(s) in ("sharepoint", "onedrive") for s in sources):
            rows = (
                await self.db.execute(
                    select(FileMetadata.id, FileMetadata.graph_item_id).where(
                        FileMetadata.organization_id == ctx.organization_id,
                        FileMetadata.user_id == ctx.user_id,
                    )
                )
            ).all()
            for row in rows:
                file_ids.add(str(row.id))
                file_ids.add(row.graph_item_id)

        if any(self._src(s) == "project" for s in sources) and project_id:
            query = (
                select(ProjectFile.id)
                .join(Project, Project.id == ProjectFile.project_id)
                .where(
                    ProjectFile.project_id == project_id,
                    ProjectFile.organization_id == ctx.organization_id,
                    Project.user_id == ctx.user_id,
                    Project.organization_id == ctx.organization_id,
                )
            )
            if sector_id:
                query = query.where(ProjectFile.sector_id == sector_id)
            rows = (await self.db.execute(query)).all()
            for row in rows:
                project_file_ids.add(str(row.id))

        return {
            "emailIds": email_ids,
            "fileIds": file_ids,
            "projectFileIds": project_file_ids,
        }

    @staticmethod
    def _src(source: VectorSource) -> str:
        return source.value if isinstance(source, VectorSource) else str(source)

    def filter_chunks(
        self, chunks: list[RetrievedChunk], allowed: dict[str, set[str]]
    ) -> list[RetrievedChunk]:
        before = len(chunks)
        filtered: list[RetrievedChunk] = []
        for c in chunks:
            src = self._src(c.metadata.source)
            if src.endswith("-workspace"):
                filtered.append(c)
                continue
            if src == "outlook":
                cid = c.metadata.email_id
                if cid and cid in allowed["emailIds"]:
                    filtered.append(c)
                continue
            if src in ("sharepoint", "onedrive"):
                if self._is_file_chunk_allowed(c, allowed["fileIds"]):
                    filtered.append(c)
                continue
            if src == "project":
                cid = c.metadata.file_id
                if cid and cid in allowed["projectFileIds"]:
                    filtered.append(c)
                continue
            filtered.append(c)

        dropped = before - len(filtered)
        if dropped > 0:
            kept_ids = {id(c) for c in filtered}
            doc_dropped = sum(
                1
                for c in chunks
                if self._src(c.metadata.source) in ("sharepoint", "onedrive")
                and id(c) not in kept_ids
            )
            if doc_dropped > 0:
                logger.warning(
                    f"[permissions] dropped {doc_dropped} document chunk(s); "
                    f"allowed file ids={len(allowed['fileIds'])}"
                )

        return filtered

    def _is_file_chunk_allowed(self, chunk: RetrievedChunk, file_ids: set[str]) -> bool:
        meta_id = chunk.metadata.file_id
        if meta_id and meta_id in file_ids:
            return True
        from_vector = self._content_id_from_vector_id(chunk.id)
        if from_vector and from_vector in file_ids:
            return True
        if not meta_id and not from_vector:
            return True
        return False

    @staticmethod
    def _content_id_from_vector_id(vid: str) -> str | None:
        match = re.match(r"^(sharepoint|onedrive):([^:]+):", vid)
        return match.group(2) if match else None


# ---------------------------------------------------------------------------
# HybridRetrievalService (hybrid-retrieval.service.ts)
# ---------------------------------------------------------------------------


class HybridRetrievalService:
    """Keyword match on indexed file names / email subjects for exact-name queries."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def find_keyword_chunks(
        self,
        ctx: AuthContext,
        query: str,
        sources: list[VectorSource],
        limit: int = 5,
        project_id: str | None = None,
        sector_id: str | None = None,
    ) -> list[RetrievedChunk]:
        term = _extract_search_term(query)
        if not term or len(term) < 3:
            return []

        chunks: list[RetrievedChunk] = []
        escaped = re.sub(r"([%_\\])", r"\\\1", term)
        pattern = f"%{escaped}%"
        src_values = [PermissionService._src(s) for s in sources]

        if "sharepoint" in src_values or "onedrive" in src_values:
            rows = (
                await self.db.execute(
                    select(
                        FileMetadata.id,
                        FileMetadata.file_name,
                        FileMetadata.web_url,
                        FileMetadata.source,
                        FileMetadata.modified_at,
                    )
                    .where(
                        FileMetadata.organization_id == ctx.organization_id,
                        FileMetadata.user_id == ctx.user_id,
                        FileMetadata.is_indexed.is_(True),
                        FileMetadata.file_name.ilike(pattern),
                    )
                    .order_by(FileMetadata.modified_at.desc().nullslast())
                    .limit(limit)
                )
            ).all()
            for f in rows:
                source = f.source.value if hasattr(f.source, "value") else str(f.source)
                if source not in src_values:
                    continue
                chunks.append(
                    RetrievedChunk(
                        id=vector_id(source, str(f.id), "0"),
                        content=f"[File: {f.file_name}]\nURL: {f.web_url or 'n/a'}",
                        score=0.95,
                        metadata=VectorMetadata(
                            source=VectorSource(source),
                            organization_id=ctx.organization_id,
                            file_id=str(f.id),
                            file_name=f.file_name,
                            web_url=f.web_url or None,
                            timestamp=_iso(f.modified_at),
                            chunk_id="0",
                        ),
                    )
                )

        if "outlook" in src_values and len(chunks) < limit:
            rows = (
                await self.db.execute(
                    select(
                        EmailMetadata.id,
                        EmailMetadata.subject,
                        EmailMetadata.sender,
                        EmailMetadata.received_at,
                    )
                    .where(
                        EmailMetadata.organization_id == ctx.organization_id,
                        EmailMetadata.user_id == ctx.user_id,
                        EmailMetadata.is_indexed.is_(True),
                        EmailMetadata.subject.ilike(pattern),
                    )
                    .order_by(EmailMetadata.received_at.desc().nullslast())
                    .limit(limit - len(chunks))
                )
            ).all()
            for e in rows:
                chunks.append(
                    RetrievedChunk(
                        id=vector_id("outlook", str(e.id), "0"),
                        content=f"Subject: {e.subject}\nFrom: {e.sender}",
                        score=0.9,
                        metadata=VectorMetadata(
                            source=VectorSource.outlook,
                            organization_id=ctx.organization_id,
                            email_id=str(e.id),
                            subject=e.subject,
                            sender=e.sender,
                            timestamp=_iso(e.received_at),
                            chunk_id="0",
                        ),
                    )
                )

        if "project" in src_values and project_id and len(chunks) < limit:
            query_stmt = (
                select(ProjectFile.id, ProjectFile.file_name, ProjectFile.created_at)
                .join(Project, Project.id == ProjectFile.project_id)
                .where(
                    ProjectFile.project_id == project_id,
                    ProjectFile.organization_id == ctx.organization_id,
                    Project.user_id == ctx.user_id,
                    ProjectFile.is_indexed.is_(True),
                    ProjectFile.file_name.ilike(pattern),
                )
            )
            if sector_id:
                query_stmt = query_stmt.where(ProjectFile.sector_id == sector_id)
            query_stmt = query_stmt.order_by(ProjectFile.created_at.desc()).limit(
                limit - len(chunks)
            )
            rows = (await self.db.execute(query_stmt)).all()
            for f in rows:
                chunks.append(
                    RetrievedChunk(
                        id=vector_id("project", str(f.id), "0"),
                        content=f"[Project file: {f.file_name}]",
                        score=0.92,
                        metadata=VectorMetadata(
                            source=VectorSource.project,
                            organization_id=ctx.organization_id,
                            project_id=project_id,
                            sector_id=sector_id if sector_id else None,
                            file_id=str(f.id),
                            file_name=f.file_name,
                            timestamp=_iso(f.created_at),
                            chunk_id="0",
                        ),
                    )
                )

        return chunks[:limit]


def _extract_search_term(query: str) -> str | None:
    q = query.strip()
    quoted = re.search(r"[\"']([^\"']{3,})[\"']", q)
    if quoted:
        return quoted.group(1)
    file_match = re.search(r"[\w.-]+\.(txt|pdf|docx|xlsx|pptx|csv|md)\b", q, re.IGNORECASE)
    if file_match:
        return file_match.group(0)
    if re.search(r"\b(find|search|locate|what does|content of)\b", q, re.IGNORECASE):
        tokens = [
            t
            for t in re.split(r"\s+", q)
            if len(t) > 3
            and not re.match(
                r"^(find|search|the|what|does|say|file|document)$", t, re.IGNORECASE
            )
        ]
        return " ".join(tokens[-2:]) or None
    return None


# ---------------------------------------------------------------------------
# OutlookMailService (outlook-mail.service.ts)
# ---------------------------------------------------------------------------


class OutlookMailService:
    """Live Outlook mail context via Microsoft Graph + email_metadata upserts."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _graph(self) -> Any | None:
        # LAZY: GraphService is a sibling module in the import cycle and may not
        # be migrated yet.
        try:
            from app.services.graph_service import GraphService
        except ImportError:
            # TODO(migration): wire to the graph module once it is ported.
            return None
        return GraphService(self.db)

    async def count_email_metadata(self, ctx: AuthContext) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(EmailMetadata)
            .where(
                EmailMetadata.organization_id == ctx.organization_id,
                EmailMetadata.user_id == ctx.user_id,
            )
        )
        return int(result.scalar_one() or 0)

    async def upsert_email_metadata(self, ctx: AuthContext, email: Any) -> str | None:
        stmt = pg_insert(EmailMetadata).values(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            graph_message_id=email["id"],
            subject=email.get("subject"),
            sender=email.get("sender"),
            received_at=_parse_dt(email.get("receivedAt")),
            conversation_id=email.get("conversationId"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                EmailMetadata.organization_id,
                EmailMetadata.user_id,
                EmailMetadata.graph_message_id,
            ],
            set_={
                "subject": stmt.excluded.subject,
                "sender": stmt.excluded.sender,
                "received_at": stmt.excluded.received_at,
                "conversation_id": stmt.excluded.conversation_id,
            },
        ).returning(EmailMetadata.id)
        result = await self.db.execute(stmt)
        await self.db.commit()
        row = result.scalar_one_or_none()
        return str(row) if row else None

    async def build_live_mail_context(
        self, ctx: AuthContext, top: int = 20
    ) -> LiveContextResult:
        graph = self._graph()
        if graph is None:
            return LiveContextResult()
        emails = await graph.fetch_recent_emails(ctx, top)
        blocks: list[LiveContextBlock] = []
        chunks: list[RetrievedChunk] = []

        for i, email in enumerate(emails):
            meta_id = await self.upsert_email_metadata(ctx, email)
            email_id = meta_id or email["id"]
            preview = (email.get("body") or "")[:1500]
            received = _format_mail_date(email.get("receivedAt"))
            content = "\n".join(
                [
                    f"**Subject:** {email.get('subject')}",
                    f"**From:** {email.get('sender')}",
                    f"**Date:** {received}",
                    f"**Read:** {'Yes' if email.get('isRead') else 'No'}",
                    "",
                    preview or "(no body)",
                ]
            )
            blocks.append(
                LiveContextBlock(
                    index=i + 1,
                    content=content,
                    label=f"Email: {email.get('subject')} from {email.get('sender')}",
                )
            )
            chunks.append(
                RetrievedChunk(
                    id=f"live:outlook:{email_id}",
                    content=content,
                    score=1,
                    metadata=VectorMetadata(
                        source=VectorSource.outlook,
                        organization_id=ctx.organization_id,
                        email_id=email_id,
                        sender=email.get("sender"),
                        timestamp=email.get("receivedAt"),
                        chunk_id="live",
                        subject=email.get("subject"),
                    ),
                )
            )

        logger.info(f"[outlook-mail] live context userId={ctx.user_id} emails={len(blocks)}")
        return LiveContextResult(blocks=blocks, chunks=chunks)

    async def is_graph_connected(self, ctx: AuthContext) -> bool:
        graph = self._graph()
        if graph is None:
            return False
        try:
            await graph.fetch_recent_emails(ctx, 1)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# SharePointDocumentsService (sharepoint-documents.service.ts)
# ---------------------------------------------------------------------------


class SharePointDocumentsService:
    """Live SharePoint/OneDrive document context via Graph + file_metadata upserts."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _graph(self) -> Any | None:
        try:
            from app.services.graph_service import GraphService
        except ImportError:
            # TODO(migration): wire to the graph module once it is ported.
            return None
        return GraphService(self.db)

    async def count_file_metadata(self, ctx: AuthContext) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(FileMetadata)
            .where(
                FileMetadata.organization_id == ctx.organization_id,
                FileMetadata.user_id == ctx.user_id,
            )
        )
        return int(result.scalar_one() or 0)

    async def upsert_file_metadata(self, ctx: AuthContext, file: Any) -> str | None:
        stmt = pg_insert(FileMetadata).values(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            graph_item_id=file["id"],
            source=file["source"],
            file_name=file["name"],
            web_url=file.get("webUrl"),
            modified_at=_parse_dt(file.get("lastModified")),
            drive_id=file.get("driveId"),
            site_id=file.get("siteId"),
            mime_type=file.get("mimeType"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                FileMetadata.organization_id,
                FileMetadata.user_id,
                FileMetadata.graph_item_id,
            ],
            set_={
                "file_name": stmt.excluded.file_name,
                "web_url": stmt.excluded.web_url,
                "modified_at": stmt.excluded.modified_at,
                "drive_id": stmt.excluded.drive_id,
                "site_id": stmt.excluded.site_id,
                "mime_type": stmt.excluded.mime_type,
            },
        ).returning(FileMetadata.id)
        result = await self.db.execute(stmt)
        await self.db.commit()
        row = result.scalar_one_or_none()
        return str(row) if row else None

    async def safe_upsert_file_metadata(self, ctx: AuthContext, file: Any) -> str | None:
        """Per-file upsert so one failure does not empty live Graph context."""
        try:
            return await self.upsert_file_metadata(ctx, file)
        except Exception as err:
            await self.db.rollback()
            logger.warning(
                f"[sharepoint-docs] metadata upsert failed file={file.get('name')}: {err}"
            )
            return None

    async def build_live_document_context(
        self, ctx: AuthContext, query: str, top: int = 15
    ) -> LiveContextResult:
        graph = self._graph()
        if graph is None:
            return LiveContextResult()
        items = await graph.search_drive_items(ctx, query, top)
        return await self._build_from_items(ctx, items, len(items))

    async def list_recent_files(self, ctx: AuthContext, top: int = 25) -> list[Any]:
        graph = self._graph()
        if graph is None:
            return []
        return await graph.fetch_recent_drive_items(ctx, top)

    async def build_recent_documents_context(
        self, ctx: AuthContext, top: int = 20
    ) -> LiveContextResult:
        files = await self.list_recent_files(ctx, -(-top // 2))  # ceil(top/2)
        return await self._build_from_items(ctx, files, top)

    async def _build_from_items(
        self, ctx: AuthContext, items: list[Any], limit: int
    ) -> LiveContextResult:
        blocks: list[LiveContextBlock] = []
        chunks: list[RetrievedChunk] = []

        for i in range(min(len(items), limit)):
            file = items[i]
            meta_id = await self.safe_upsert_file_metadata(ctx, file)
            file_id = meta_id or file["id"]
            modified = _format_file_date(file.get("lastModified"))
            site_name = file.get("siteName")
            lines = [
                f"**File:** {file['name']}",
                f"**Site:** {site_name}" if site_name else "",
                f"**Source:** {file['source']}",
                f"**Modified:** {modified}",
                f"**Link:** {file['webUrl']}" if file.get("webUrl") else "",
            ]
            content = "\n".join(line for line in lines if line)

            blocks.append(
                LiveContextBlock(
                    index=i + 1,
                    content=content,
                    label=f"{file['source']}: {file['name']}",
                )
            )
            chunks.append(
                RetrievedChunk(
                    id=f"live:{file['source']}:{file_id}",
                    content=content,
                    score=1,
                    metadata=VectorMetadata(
                        source=VectorSource(file["source"]),
                        organization_id=ctx.organization_id,
                        file_id=file_id,
                        file_name=file["name"],
                        timestamp=file.get("lastModified"),
                        chunk_id="live",
                        web_url=file.get("webUrl"),
                    ),
                )
            )

        logger.info(
            f"[sharepoint-docs] live context userId={ctx.user_id} files={len(blocks)}"
        )
        return LiveContextResult(blocks=blocks, chunks=chunks)


# ---------------------------------------------------------------------------
# RagService (rag.service.ts)
# ---------------------------------------------------------------------------


class RagService:
    """Retrieval-augmented generation over the user's Microsoft 365 data."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.ai = AiClientService()
        self.permissions = PermissionService(db)
        self.redis = RedisService()
        self.outlook_mail = OutlookMailService(db)
        self.sharepoint_docs = SharePointDocumentsService(db)
        self.hybrid = HybridRetrievalService(db)

    async def query(
        self,
        ctx: AuthContext,
        query: str,
        options: RagQueryOptions | dict | None = None,
    ) -> RagQueryResponse:
        options = _coerce_rag_options(options)

        if not options.bypass_topic_guard:
            scope = assert_in_scope_query(query)
            if not scope["in_scope"]:
                logger.info(
                    f"[rag] out-of-scope query userId={ctx.user_id} "
                    f"reason={scope['reason'] or 'unknown'}"
                )
                response = RagQueryResponse(
                    answer=OUT_OF_SCOPE_REFUSAL_MESSAGE,
                    citations=[],
                    chunks=[],
                    scope_reason=scope["reason"],
                    empty_reason="out_of_scope",
                )
                self._log_ai_response("empty", query, response)
                return response

        project_only = options.project_only is True

        # M365 recency / live-context shortcuts never apply to project-only chats.
        mail_recency = not project_only and is_mail_recency_query(query)
        document_recency = not project_only and is_document_recency_query(query)

        if project_only:
            sources: list[VectorSource] = [VectorSource.project]
        else:
            default_sources: list[VectorSource] = [
                VectorSource.outlook,
                VectorSource.sharepoint,
                VectorSource.onedrive,
            ]
            if not mail_recency and not document_recency and not options.sources:
                classified = await self.ai.classify_intent(query)
                suggested = (classified or {}).get("suggestedSources")
                if suggested:
                    default_sources = [VectorSource(s) for s in suggested]

            if mail_recency:
                sources = [VectorSource.outlook]
            elif document_recency:
                sources = [VectorSource.sharepoint, VectorSource.onedrive]
            else:
                sources = options.sources or default_sources

            if options.project_id and VectorSource.project not in sources:
                sources = [*sources, VectorSource.project]

        top_k = options.top_k or DEFAULT_TOP_K

        live_prefix = (
            "live-mail:" if mail_recency else "live-docs:" if document_recency else ""
        )
        # Scope the cache by project/workspace so project knowledge answers never
        # leak across projects or in from the General context.
        scope_key = (
            f"p:{options.project_id or '-'}:s:{options.sector_id or '-'}"
            f":w:{options.workspace_id or '-'}"
        )
        query_hash = base64.b64encode(query.encode("utf-8")).decode("ascii")[:32]
        cache_key = (
            f"rag:{ctx.organization_id}:{scope_key}:{live_prefix}{query_hash}"
        )

        cached = await self.redis.get_cached(cache_key)
        if cached:
            cached_response = RagQueryResponse.model_validate(cached)
            self._log_ai_response("cache", query, cached_response)
            return cached_response

        ranked_chunks: list[RetrievedChunk] = []
        used_live_context = False

        if mail_recency:
            live = await self._try_live_mail_context(ctx)
            if live and live.chunks:
                ranked_chunks = live.chunks
                used_live_context = True
                self._schedule_background_sync(ctx)
        elif document_recency:
            live = await self._try_live_document_context(ctx, query)
            if live and live.chunks:
                ranked_chunks = live.chunks
                used_live_context = True
                self._schedule_background_sync(ctx)

        if not used_live_context:
            keyword_chunks = await self.hybrid.find_keyword_chunks(
                ctx, query, sources, 5, options.project_id, options.sector_id
            )
            ranked_chunks = await self._retrieve_from_pinecone(
                ctx, query, sources, top_k, options
            )
            if keyword_chunks:
                seen = {c.id for c in ranked_chunks}
                for k in keyword_chunks:
                    if k.id not in seen:
                        ranked_chunks.insert(0, k)
                        seen.add(k.id)
                ranked_chunks = ranked_chunks[:top_k]

            has_outlook = any(
                PermissionService._src(c.metadata.source) == "outlook" for c in ranked_chunks
            )
            if not has_outlook and VectorSource.outlook in sources:
                connected = await self.outlook_mail.is_graph_connected(ctx)
                if connected:
                    live = await self._try_live_mail_context(ctx)
                    if live:
                        ranked_chunks = live.chunks
                        used_live_context = True
                        self._schedule_background_sync(ctx)

            has_doc = any(
                PermissionService._src(c.metadata.source) in ("sharepoint", "onedrive")
                for c in ranked_chunks
            )
            if not has_doc and (
                VectorSource.sharepoint in sources or VectorSource.onedrive in sources
            ):
                live = await self._try_live_document_context(ctx, query)
                if live and live.chunks:
                    ranked_chunks = live.chunks
                    used_live_context = True
                    self._schedule_background_sync(ctx)

        workspace_instructions: str | None = None
        project_instructions: str | None = None

        if options.project_id:
            project_ctx = await self._get_project_instructions(ctx, options.project_id)
            project_instructions = project_ctx
        elif options.workspace_id:
            row = (
                await self.db.execute(
                    select(WorkspaceInstructions.instructions)
                    .where(
                        WorkspaceInstructions.workspace_id == options.workspace_id,
                        WorkspaceInstructions.is_active.is_(True),
                    )
                    .order_by(WorkspaceInstructions.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            workspace_instructions = row

        context_blocks = [
            {"index": i + 1, "content": c.content, "label": self._format_label(c)}
            for i, c in enumerate(ranked_chunks)
        ]

        if not context_blocks:
            return await self._empty_response(
                ctx, query, project_only, document_recency, mail_recency
            )

        answer = await self.ai.chat(
            messages=[
                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_rag_user_prompt(
                        query,
                        context_blocks,
                        workspace_instructions,
                        project_instructions,
                    ),
                },
            ],
            model=LLM_MODEL,
            temperature=0.2,
        )

        citations = self._build_citations(ranked_chunks)
        response = RagQueryResponse(answer=answer, citations=citations, chunks=ranked_chunks)

        if not used_live_context:
            # Cache with field names (not serialization aliases) so the cached
            # payload re-validates cleanly on read; the frontend JSON shape is
            # produced separately when the consuming router serializes the model.
            await self.redis.set_cached(cache_key, response.model_dump(mode="json"), 120)

        self._log_ai_response(
            "live" if used_live_context else "llm",
            query,
            response,
            {"model": LLM_MODEL, "chunk_count": len(ranked_chunks)},
        )
        return response

    # --- live context helpers --------------------------------------------

    async def _try_live_mail_context(self, ctx: AuthContext) -> LiveContextResult | None:
        try:
            return await self.outlook_mail.build_live_mail_context(ctx, 20)
        except Exception as err:
            logger.warning(f"[rag] live mail context failed userId={ctx.user_id}: {err}")
            return None

    async def _try_live_document_context(
        self, ctx: AuthContext, query: str
    ) -> LiveContextResult | None:
        try:
            search_result = await self.sharepoint_docs.build_live_document_context(
                ctx, query, 15
            )
            if search_result.chunks:
                return search_result
            recent = await self.sharepoint_docs.build_recent_documents_context(ctx, 20)
            if recent.chunks:
                return recent
            return None
        except Exception as err:
            logger.warning(
                f"[rag] live document context failed userId={ctx.user_id}: {err}"
            )
            return None

    def _schedule_background_sync(self, ctx: AuthContext) -> None:
        import asyncio

        # LAZY: ingestion is a sibling in the import cycle and may not be migrated.
        try:
            from app.services.ingestion_service import IngestionService
        except ImportError:
            # TODO(migration): wire to the ingestion module once it is ported
            # (fire-and-forget background Outlook + documents sync).
            logger.info(
                f"[rag] background sync pending ingestion migration userId={ctx.user_id}"
            )
            return

        ingestion = IngestionService(self.db)

        async def _sync() -> None:
            try:
                await ingestion.sync_outlook(ctx)
            except Exception as err:
                logger.warning(f"[rag] background outlook sync failed: {err}")
            try:
                await ingestion.sync_all_documents(ctx)
            except Exception as err:
                logger.warning(f"[rag] background documents sync failed: {err}")

        try:
            asyncio.get_running_loop().create_task(_sync())
        except RuntimeError:
            pass

    async def _get_project_instructions(
        self, ctx: AuthContext, project_id: str
    ) -> str | None:
        # LAZY: projects is a sibling in the import cycle and may not be migrated.
        try:
            from app.services.projects_service import ProjectsService
        except ImportError:
            # TODO(migration): use ProjectsService.get_instructions_for_rag once
            # the projects module is ported. Fall back to a direct read so RAG
            # still applies a project's custom instructions.
            row = (
                await self.db.execute(
                    select(Project.custom_instructions).where(
                        Project.id == project_id,
                        Project.organization_id == ctx.organization_id,
                        Project.user_id == ctx.user_id,
                    )
                )
            ).scalar_one_or_none()
            return row

        projects = ProjectsService(self.db)
        project_ctx = await projects.get_instructions_for_rag(ctx, project_id)
        return project_ctx.get("instructions")

    # --- pinecone retrieval ----------------------------------------------

    async def _retrieve_from_pinecone(
        self,
        ctx: AuthContext,
        query: str,
        sources: list[VectorSource],
        top_k: int,
        options: RagQueryOptions,
    ) -> list[RetrievedChunk]:
        pinecone = self._pinecone()
        if pinecone is None:
            return []

        embed = await self.ai.embed([query])
        query_vector = embed["embeddings"][0]

        allowed = await self.permissions.get_allowed_content_ids(
            ctx, sources, options.project_id, options.sector_id
        )

        lists: list[list[dict[str, Any]]] = []
        chunk_map: dict[str, RetrievedChunk] = {}

        for source in sources:
            src = PermissionService._src(source)
            filter_: dict[str, Any] = {
                "source": {"$eq": src},
                "organizationId": {"$eq": ctx.organization_id},
            }
            if options.workspace_id:
                filter_["workspaceId"] = {"$eq": options.workspace_id}
            if src == "project" and options.project_id:
                filter_["projectId"] = {"$eq": options.project_id}
                if options.sector_id:
                    filter_["sectorId"] = {"$eq": options.sector_id}

            hits = await pinecone.query(ctx.organization_id, query_vector, top_k, filter_)
            filtered = self.permissions.filter_chunks(hits, allowed)
            lists.append([{"id": h.id, "score": h.score} for h in filtered])
            for h in filtered:
                chunk_map[h.id] = h

        fused = reciprocal_rank_fusion(lists)[:top_k]
        ranked_chunks = [
            chunk_map[f["id"]] for f in fused if f["id"] in chunk_map
        ]

        reranked = await self.ai.rerank(
            query,
            [{"id": c.id, "content": c.content, "score": c.score} for c in ranked_chunks],
            RERANK_TOP_K,
        )
        result = [
            chunk_map[r["id"]] for r in reranked if r["id"] in chunk_map
        ][:RERANK_TOP_K]

        if is_document_content_query(query):
            result = self._ensure_document_chunks_in_result(result, chunk_map, RERANK_TOP_K)

        if not result and chunk_map:
            logger.warning(
                f"[rag] retrieveFromPinecone: {len(chunk_map)} chunks before rerank "
                f"but 0 after; sources={','.join(PermissionService._src(s) for s in sources)}"
            )

        return result

    def _pinecone(self) -> Any | None:
        # LAZY: pinecone is a sibling in the import cycle and may not be migrated.
        try:
            from app.services.pinecone_service import PineconeService
        except ImportError:
            # TODO(migration): wire to the pinecone module once it is ported.
            # Without it, Pinecone retrieval is skipped (live/keyword paths still work).
            return None
        return PineconeService()

    def _ensure_document_chunks_in_result(
        self,
        result: list[RetrievedChunk],
        chunk_map: dict[str, RetrievedChunk],
        limit: int,
    ) -> list[RetrievedChunk]:
        """Prevent Outlook-only rerank from hiding indexed SharePoint/OneDrive chunks."""
        has_doc = any(
            PermissionService._src(c.metadata.source) in ("sharepoint", "onedrive")
            for c in result
        )
        if has_doc:
            return result

        doc_chunks = sorted(
            (
                c
                for c in chunk_map.values()
                if PermissionService._src(c.metadata.source) in ("sharepoint", "onedrive")
            ),
            key=lambda c: c.score,
            reverse=True,
        )[: min(3, limit)]

        if not doc_chunks:
            return result

        seen = {c.id for c in result}
        merged = [c for c in doc_chunks if c.id not in seen] + result
        return merged[:limit]

    # --- empty / formatting / logging ------------------------------------

    async def _empty_response(
        self,
        ctx: AuthContext,
        query: str,
        project_only: bool,
        document_recency: bool,
        mail_recency: bool,
    ) -> RagQueryResponse:
        if project_only:
            response = RagQueryResponse(
                answer=(
                    "I couldn't find anything about that in this project's knowledge "
                    "base. Upload the relevant files in project settings, or rephrase "
                    "your question to match the documents in this project."
                ),
                citations=[],
                chunks=[],
                empty_reason="no_indexed_data",
            )
            self._log_ai_response("empty", query, response)
            return response

        connected = await self.outlook_mail.is_graph_connected(ctx)
        empty_reason = "no_indexed_data"
        if not connected:
            answer = (
                "Microsoft 365 is not connected. Sign in again with Microsoft from "
                "the login page."
            )
            empty_reason = "not_connected"
        elif document_recency:
            answer = EMPTY_DOCUMENTS_MESSAGE
        elif mail_recency:
            answer = EMPTY_MAILBOX_MESSAGE
        else:
            answer = (
                "No matching content was found in your indexed mailbox or documents. "
                "Try syncing from Settings or rephrase your question."
            )

        response = RagQueryResponse(
            answer=answer, citations=[], chunks=[], empty_reason=empty_reason
        )
        self._log_ai_response("empty", query, response)
        return response

    def _log_ai_response(
        self,
        source: str,
        user_query: str,
        response: RagQueryResponse,
        extra: dict[str, Any] | None = None,
    ) -> None:
        meta = {
            "source": source,
            "queryPreview": user_query[:120],
            "answerLength": len(response.answer),
            "citationCount": len(response.citations),
            **(extra or {}),
        }
        logger.info(f"[rag] AI response {meta}")
        logger.info(f"[rag] answer:\n{response.answer}")

    def _format_label(self, chunk: RetrievedChunk) -> str:
        m = chunk.metadata
        src = PermissionService._src(m.source)
        if src == "outlook":
            return f"Email: {m.subject or 'Unknown'} from {m.sender or 'unknown'}"
        if src in ("sharepoint", "onedrive"):
            return f"Document ({src}): {m.file_name or m.file_id or 'file'}"
        if src == "project":
            return f"Project file: {m.file_name or m.file_id or 'file'}"
        return f"{src}: {m.file_name or m.file_id or 'document'}"

    def _build_citations(self, chunks: list[RetrievedChunk]) -> list[Citation]:
        return [
            Citation(
                index=i + 1,
                source=c.metadata.source,
                title=c.metadata.subject or c.metadata.file_name or c.id,
                snippet=c.content[:200],
                url=c.metadata.web_url,
                timestamp=c.metadata.timestamp,
            )
            for i, c in enumerate(chunks)
        ]


# ---------------------------------------------------------------------------
# Local datetime helpers
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _format_mail_date(iso: Any) -> str:
    """Mirror the TS ``toLocaleString('en-US', ...)`` short date+time format.

    Implemented without platform-specific strftime padding (``%-d`` is not
    portable to Windows).
    """
    dt = _parse_dt(iso)
    if dt is None:
        return str(iso) if iso else ""
    hour12 = dt.hour % 12 or 12
    meridiem = "AM" if dt.hour < 12 else "PM"
    return (
        f"{_MONTHS[dt.month - 1]} {dt.day}, {dt.year}, "
        f"{hour12}:{dt.minute:02d} {meridiem}"
    )


def _format_file_date(iso: Any) -> str:
    if not iso:
        return "Unknown"
    dt = _parse_dt(iso)
    if dt is None:
        return str(iso)
    return f"{_MONTHS[dt.month - 1]} {dt.day}, {dt.year}"
