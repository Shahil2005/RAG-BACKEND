"""Search orchestration — ORM port of the NestJS SearchModule.

Ports two NestJS providers:
  - ``SearchService``          (search.service.ts)  -> ``SearchService``
  - ``TavilyResearchService``  (tavily-research.service.ts) -> ``TavilyResearchService``

The unified/internet endpoints delegate to the query orchestrator
(``QueryService.unified_search``). That sibling is imported lazily inside the
methods because the rag/ingestion/query/search modules form an import cycle.

Audit logging mirrors the NestJS ``AuditService.logAudit`` raw-SQL insert into the
``audit_logs`` table (owned by the common/audit module, hence a Core ``text()``
insert here rather than a redefined ORM model).
"""

import json
import os

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import logger
from app.schema.auth import AuthContext
from app.schema.common import ExternalResearchChunk, QueryIntent, UnifiedSearchRequest, UnifiedSearchResponse

_TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilyResearchService:
    """Business web research via the Tavily API (port of tavily-research.service.ts)."""

    def is_enabled(self) -> bool:
        if os.environ.get("ENABLE_BUSINESS_RESEARCH") == "false":
            return False
        return bool((os.environ.get("TAVILY_API_KEY") or "").strip())

    def build_search_query(self, query: str, intent: QueryIntent) -> str:
        if intent in (QueryIntent.business_research, QueryIntent.hybrid):
            return f"{query.strip()} commercial business pricing benchmark industry"
        return query.strip()

    async def search_business_web(
        self,
        query: str,
        intent: QueryIntent,
        max_results: int = 5,
    ) -> list[ExternalResearchChunk]:
        api_key = (os.environ.get("TAVILY_API_KEY") or "").strip()
        if not api_key or not self.is_enabled():
            return []

        search_query = self.build_search_query(query, intent)

        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    _TAVILY_ENDPOINT,
                    headers={"Content-Type": "application/json"},
                    json={
                        "api_key": api_key,
                        "query": search_query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                )

            if res.status_code >= 400:
                logger.warning(f"[tavily] search failed status={res.status_code}")
                return []

            data = res.json()
            results = data.get("results") or []
            return [
                ExternalResearchChunk(
                    id=f"ext-{i + 1}",
                    index=i + 1,
                    title=r.get("title"),
                    content=r.get("content"),
                    url=r.get("url"),
                    source="external",
                )
                for i, r in enumerate(results)
            ]
        except Exception as err:
            logger.warning(f"[tavily] search error: {err}")
            return []


class SearchService:
    """Unified/internet search endpoints (port of search.service.ts)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def unified_search(
        self,
        ctx: AuthContext,
        body: UnifiedSearchRequest,
    ) -> UnifiedSearchResponse:
        # Lazy import: rag/ingestion/query/search form an import cycle.
        from app.services.query_service import QueryService

        orchestrator = QueryService(self.db)
        result = await orchestrator.unified_search(
            ctx,
            body.query,
            {
                "workspaceId": body.workspace_id,
                "sources": body.sources,
                "topK": body.top_k,
            },
        )

        await self._log_audit(
            ctx,
            "search.unified",
            "search",
            None,
            {
                "query": body.query,
                "intent": result.intent,
                "usedExternal": result.used_external_search,
            },
        )

        return result

    async def internet_search(
        self,
        ctx: AuthContext,
        query: str,
    ) -> UnifiedSearchResponse:
        from app.services.query_service import QueryService

        orchestrator = QueryService(self.db)
        result = await orchestrator.unified_search(
            ctx,
            query,
            {"forceExternal": True},
        )

        await self._log_audit(
            ctx,
            "search.internet",
            "search",
            None,
            {"query": query, "intent": result.intent},
        )

        return result

    async def _log_audit(
        self,
        ctx: AuthContext,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        # Mirrors AuditService.logAudit; audit_logs is owned by the common/audit
        # module, so insert via Core text() rather than redefining its ORM model.
        def _coerce(value: object) -> object:
            return value.value if hasattr(value, "value") else value

        payload = {k: _coerce(v) for k, v in (metadata or {}).items()}
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
                "metadata": json.dumps(payload),
            },
        )
        await self.db.commit()
