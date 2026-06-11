"""Query orchestration (port of the NestJS query module).

Ports two source files:

* ``query-intent.util.ts`` — intent classification, off-topic detection, and
  document-request parsing. These are pure functions and are exposed here as
  module-level helpers (other modules — e.g. ``rag``'s topic guard — import
  ``classify_query_intent`` / ``assert_in_scope_query``).
* ``query-orchestration.service.ts`` — ``QueryOrchestrationService``, which routes
  a user query to document generation, project-scoped RAG, Microsoft 365 RAG,
  external business web research, or a hybrid synthesis of internal + external.

Sibling services (``rag``, ``documents``, ``search``/Tavily, ``common``/audit)
form an import cycle, so they are imported LAZILY inside the methods that use
them — never at module top level.
"""

import os
import re

from app import logger
from app.core.constants import LLM_MODEL
from app.core.utils import normalize_keys
from app.prompts import BUSINESS_RESEARCH_SYSTEM_PROMPT, build_hybrid_synthesis_user_prompt
from app.schema.auth import AuthContext
from app.schema.common import (
    Citation,
    ExternalResearchChunk,
    QueryIntent,
    RagQueryResponse,
    UnifiedSearchResponse,
    VectorSource,
)
from app.schema.query import DocumentRequest, OrchestratedQueryOptions


def _coerce_query_options(
    options: "OrchestratedQueryOptions | dict | None",
) -> OrchestratedQueryOptions:
    """Accept an OrchestratedQueryOptions, a (camelCase/snake_case) dict, or None.

    Cross-service callers pass option dicts with inconsistent key casing; normalize
    them to the model's snake_case fields.
    """
    if options is None:
        return OrchestratedQueryOptions()
    if isinstance(options, dict):
        valid = set(OrchestratedQueryOptions.model_fields)
        return OrchestratedQueryOptions(
            **{k: v for k, v in normalize_keys(options).items() if k in valid}
        )
    return options

# ---------------------------------------------------------------------------
# query-intent.util.ts — patterns
# ---------------------------------------------------------------------------

_OFF_TOPIC_PATTERNS = [
    re.compile(r"\bweather\b", re.IGNORECASE),
    re.compile(r"\bforecast\b", re.IGNORECASE),
    re.compile(r"\b(sports?|cricket|football|nba|nfl)\b", re.IGNORECASE),
    re.compile(r"\b(recipe|cook|restaurant)\b", re.IGNORECASE),
    re.compile(r"\b(latest\s+news|breaking\s+news|headlines)\b", re.IGNORECASE),
    re.compile(
        r"\bwho\s+(is|was|won)\s+the\s+(president|election|match|game)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(write|generate)\s+(me\s+)?(python|javascript|java|code)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(movie|netflix|song|lyrics)\b", re.IGNORECASE),
    re.compile(r"\b(crypto|bitcoin|stock\s+price)\b", re.IGNORECASE),
]

OFF_TOPIC_REFUSAL_MESSAGE = (
    "I can help with AppXcess Technologies work: your Microsoft 365 mail and "
    "documents, plus business research such as industry benchmarks, vendor or "
    "company lookup, and pricing references. Personal topics like weather or "
    "sports are not supported."
)

_M365_KEYWORD_PATTERNS = [
    re.compile(r"\boutlook\b", re.IGNORECASE),
    re.compile(r"\b(email|e-mail|mail|mailbox|inbox)\b", re.IGNORECASE),
    re.compile(r"\bsharepoint\b", re.IGNORECASE),
    re.compile(r"\bonedrive\b", re.IGNORECASE),
    re.compile(r"\b(document|documents|file|files)\b", re.IGNORECASE),
    re.compile(r"\b(message|messages|thread|conversation)\b", re.IGNORECASE),
    re.compile(r"\b(sender|subject|unread)\b", re.IGNORECASE),
    re.compile(r"\b(workspace)\b", re.IGNORECASE),
    re.compile(r"\b(microsoft\s+365|m365)\b", re.IGNORECASE),
    re.compile(r"\b(classif(y|ication)|spam|important)\b", re.IGNORECASE),
]

BUSINESS_RESEARCH_PATTERNS = [
    re.compile(r"\bindustry\s+average\b", re.IGNORECASE),
    re.compile(r"\bbenchmark\b", re.IGNORECASE),
    re.compile(r"\bmarket\s+rates?\b", re.IGNORECASE),
    re.compile(r"\bmarket\s+trends?\b", re.IGNORECASE),
    re.compile(r"\btypical\s+cost\b", re.IGNORECASE),
    re.compile(r"\bmarket\s+size\b", re.IGNORECASE),
    re.compile(r"\bmarket\s+research\b", re.IGNORECASE),
    re.compile(r"\b(vendor|supplier)\b", re.IGNORECASE),
    re.compile(r"\bcompany\s+profile\b", re.IGNORECASE),
    re.compile(r"\bresearch\s+(a\s+)?company\b", re.IGNORECASE),
    re.compile(r"\bresearch\s+vendor\b", re.IGNORECASE),
    re.compile(r"\bpricing\b", re.IGNORECASE),
    re.compile(r"\bprice\s+list\b", re.IGNORECASE),
    re.compile(r"\bquote\b", re.IGNORECASE),
    re.compile(r"\bRFP\b", re.IGNORECASE),
    re.compile(r"\bcost\s+per\b", re.IGNORECASE),
    re.compile(r"\bcompetitor(s)?\b", re.IGNORECASE),
    re.compile(r"\bstartups?\b", re.IGNORECASE),
    re.compile(r"\brate\s+card\b", re.IGNORECASE),
    re.compile(r"\bcurrent\s+pricing\b", re.IGNORECASE),
    re.compile(r"\bpulling\s+pricing\b", re.IGNORECASE),
    re.compile(
        r"\bother\s+(startups?|companies|firms|players|vendors?)\b", re.IGNORECASE
    ),
    re.compile(r"\bcompare\b.*\b(with|to|against|versus)\b", re.IGNORECASE),
    re.compile(r"\b(versus|vs\.?)\s+", re.IGNORECASE),
    re.compile(r"\bhow\s+does\s+.+\s+compare\b", re.IGNORECASE),
]

# Implicit (natural-language) document-generation gates.
_DOC_GEN_ACTION = re.compile(
    r"\b(draft|generate|create|write|prepare|compose|put\s+together|make)\b",
    re.IGNORECASE,
)
_DOC_GEN_NOUN = re.compile(
    r"\b(estimate|job\s+summary|quotation|quote|customer(?:[-\s]facing)?\s+email|"
    r"proposal|report|invoice)\b",
    re.IGNORECASE,
)
_DOC_GEN_USING_TEMPLATE = re.compile(r"\busing\b[^.]*\btemplate\b", re.IGNORECASE)
# ``/document``, ``/doc``, ``/docs`` (optionally followed by the request text).
_DOC_SLASH = re.compile(r"^/(documents?|docs?)\b[ \t]*", re.IGNORECASE)


# ---------------------------------------------------------------------------
# query-intent.util.ts — pure helpers
# ---------------------------------------------------------------------------


def _get_appxcess_aliases() -> list[str]:
    raw = os.environ.get(
        "APPXCESS_TOPIC_ALIASES", "AppXcess,AppXcess Technologies,Starbot"
    )
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def is_off_topic_general_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _OFF_TOPIC_PATTERNS)


def is_external_comparison_query(query: str) -> bool:
    """Compare/contrast with industry, competitors, or public market — needs web."""
    normalized = query.strip()
    if not normalized:
        return False
    return (
        re.search(
            r"\bcompare\b.*\b(with|to|against|other|industry|market|startups?|"
            r"competitors?)\b",
            normalized,
            re.IGNORECASE,
        )
        is not None
        or re.search(r"\bmarket\s+trends?\b", normalized, re.IGNORECASE) is not None
        or re.search(
            r"\bother\s+(startups?|companies)\b", normalized, re.IGNORECASE
        )
        is not None
        or re.search(
            r"\b(industry|public)\s+(average|benchmark|landscape)\b",
            normalized,
            re.IGNORECASE,
        )
        is not None
    )


def is_business_research_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    return any(
        p.search(normalized) for p in BUSINESS_RESEARCH_PATTERNS
    ) or is_external_comparison_query(normalized)


def is_m365_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False

    # Lazy import: rag module's recency/content query helpers (cycle). The rag
    # module is a sibling in the rag/ingestion/query/search cycle, so this is
    # imported here rather than at module top level. If rag is not yet ported,
    # degrade gracefully — the keyword/alias gates below still classify M365.
    try:
        from app.services.rag_service import (  # type: ignore[attr-defined]
            is_document_content_query,
            is_document_recency_query,
            is_mail_recency_query,
        )

        if (
            is_mail_recency_query(normalized)
            or is_document_recency_query(normalized)
            or is_document_content_query(normalized)
        ):
            return True
    except ImportError:
        # TODO(migration): remove this guard once the `rag` module is ported and
        # exposes is_mail_recency_query / is_document_recency_query /
        # is_document_content_query.
        logger.debug("[query] rag query-util helpers unavailable (rag not ported)")

    if any(p.search(normalized) for p in _M365_KEYWORD_PATTERNS):
        return True

    lower = normalized.lower()
    if any(alias in lower for alias in _get_appxcess_aliases()):
        # Company name alone is not enough for m365_only when the user wants an
        # external comparison.
        if is_business_research_query(normalized):
            return True
        return not is_external_comparison_query(normalized)
    return False


def parse_document_request(query: str) -> DocumentRequest | None:
    """Detect a document-generation request, or ``None`` for an ordinary query.

    Triggers explicitly via ``/document ...`` or implicitly when an action verb
    co-occurs with a document noun (or a "using ... template" phrase).
    """
    normalized = query.strip()
    if not normalized:
        return None

    if _DOC_SLASH.search(normalized):
        return DocumentRequest(
            description=_DOC_SLASH.sub("", normalized).strip(), explicit=True
        )

    if _DOC_GEN_ACTION.search(normalized) and (
        _DOC_GEN_NOUN.search(normalized) or _DOC_GEN_USING_TEMPLATE.search(normalized)
    ):
        return DocumentRequest(description=normalized, explicit=False)

    return None


def classify_query_intent(query: str, chat_history: list[dict] | None = None) -> QueryIntent:
    normalized = query.strip()
    if not normalized:
        return QueryIntent.off_topic

    if parse_document_request(normalized):
        return QueryIntent.document_generation

    if is_off_topic_general_query(normalized):
        return QueryIntent.off_topic

    m365 = is_m365_query(normalized)
    business = is_business_research_query(normalized)

    if m365 and business:
        return QueryIntent.hybrid
    if business:
        return QueryIntent.business_research
    if m365:
        return QueryIntent.m365_only

    if chat_history and len(chat_history) > 0:
        return QueryIntent.m365_only

    return QueryIntent.off_topic


def assert_in_scope_query(query: str) -> dict:
    """Used by the topic guard — in scope if not ``off_topic``.

    Returns ``{"inScope": bool, "reason"?: str, "intent"?: QueryIntent}``.
    """
    intent = classify_query_intent(query)
    if intent == QueryIntent.off_topic:
        normalized = query.strip()
        if not normalized:
            return {"inScope": False, "reason": "empty_query", "intent": intent}
        return {"inScope": False, "reason": "not_work_related", "intent": intent}
    return {"inScope": True, "intent": intent}


# ---------------------------------------------------------------------------
# query-orchestration.service.ts
# ---------------------------------------------------------------------------


class QueryOrchestrationService:
    """Routes a user query across document generation, RAG, and web research.

    Mirrors the NestJS ``QueryOrchestrationService``. Sibling services (rag,
    documents, search/Tavily, common/audit) are imported lazily inside methods
    to avoid the rag/ingestion/query/search import cycle at boot.
    """

    def __init__(self, db) -> None:  # noqa: ANN001 - AsyncSession (untyped to avoid import)
        self.db = db

    # --- sibling accessors (lazy) ----------------------------------------

    def _rag(self):
        from app.services.rag_service import RagService

        return RagService(self.db)

    def _tavily(self):
        from app.services.search_service import TavilyResearchService

        return TavilyResearchService()

    def _audit(self):
        from app.services.audit_service import AuditService

        return AuditService(self.db)

    def _doc_gen(self):
        from app.services.documents_service import DocumentGenerationService

        return DocumentGenerationService(self.db)

    def _ai(self):
        from app.services.ai_client import AiClientService

        return AiClientService()

    # --- public API ------------------------------------------------------

    async def query(
        self,
        ctx: AuthContext,
        query: str,
        options: OrchestratedQueryOptions | dict | None = None,
    ) -> RagQueryResponse:
        options = _coerce_query_options(options)

        # Document generation takes priority and works in any chat (main or
        # project): detect "/document ..." or a natural-language draft request
        # and route it to the template-driven generator before any RAG/scope
        # handling.
        doc_request = parse_document_request(query)
        if doc_request:
            await self._audit().log_audit(
                ctx,
                "query.document_generation",
                "query",
                None,
                {
                    "explicit": doc_request.explicit,
                    "projectId": options.project_id,
                    "queryPreview": query[:120],
                },
            )
            return await self._doc_gen().generate_from_prompt(
                ctx,
                doc_request,
                {
                    "projectId": options.project_id,
                    "workspaceId": options.workspace_id,
                },
            )

        # Project chats are strictly scoped to the project's uploaded knowledge
        # base. No Microsoft 365 sources, no external/business web research, and
        # no off-topic refusal — every question is answered only from project
        # files.
        if options.project_id:
            await self._audit().log_audit(
                ctx,
                "query.project",
                "query",
                None,
                {
                    "projectId": options.project_id,
                    "sectorId": options.sector_id,
                    "queryPreview": query[:120],
                },
            )
            result = await self._rag().query(
                ctx,
                query,
                {
                    "projectId": options.project_id,
                    "sectorId": options.sector_id,
                    "sources": [VectorSource.project],
                    "topK": options.top_k,
                    "bypassTopicGuard": True,
                    "projectOnly": True,
                    "chatHistory": options.chat_history,
                },
            )
            return result.model_copy(
                update={
                    "intent": QueryIntent.m365_only,
                    "used_external_search": False,
                }
            )

        intent: QueryIntent = (
            QueryIntent.business_research
            if options.force_external
            else classify_query_intent(query, options.chat_history)
        )

        if intent == QueryIntent.off_topic:
            return RagQueryResponse(
                answer=OFF_TOPIC_REFUSAL_MESSAGE,
                citations=[],
                chunks=[],
                intent=intent,
                empty_reason="out_of_scope",
                scope_reason="not_work_related",
            )

        await self._audit().log_audit(
            ctx,
            "query.orchestrated",
            "query",
            None,
            {"intent": intent.value, "queryPreview": query[:120]},
        )

        if intent == QueryIntent.m365_only:
            result = await self._rag().query(
                ctx,
                query,
                {
                    "workspaceId": options.workspace_id,
                    "projectId": options.project_id,
                    "sources": options.sources,
                    "topK": options.top_k,
                    "bypassTopicGuard": True,
                    "chatHistory": options.chat_history,
                },
            )
            return result.model_copy(
                update={"intent": intent, "used_external_search": False}
            )

        if intent == QueryIntent.business_research:
            return await self._run_business_research(ctx, query, intent, None, options.chat_history)

        internal = await self._rag().query(
            ctx,
            query,
            {
                "workspaceId": options.workspace_id,
                "projectId": options.project_id,
                "sources": options.sources,
                "topK": options.top_k if options.top_k is not None else 5,
                "bypassTopicGuard": True,
                "chatHistory": options.chat_history,
            },
        )
        return await self._run_business_research(
            ctx, query, QueryIntent.hybrid, internal, options.chat_history
        )

    async def unified_search(
        self,
        ctx: AuthContext,
        query: str,
        options: OrchestratedQueryOptions | dict | None = None,
    ) -> UnifiedSearchResponse:
        result = await self.query(ctx, query, options)
        return UnifiedSearchResponse(
            answer=result.answer,
            citations=result.citations,
            internal_results=result.chunks,
            external_results=result.external_results,
            intent=result.intent,
            used_external_search=result.used_external_search,
        )

    # --- internal --------------------------------------------------------

    async def _run_business_research(
        self,
        ctx: AuthContext,
        query: str,
        intent: QueryIntent,
        internal: RagQueryResponse | None,
        chat_history: list[dict] | None = None,
    ) -> RagQueryResponse:
        tavily = self._tavily()
        external_chunks: list[ExternalResearchChunk] = await tavily.search_business_web(
            query, intent
        )
        used_external = len(external_chunks) > 0

        if not used_external and not tavily.is_enabled():
            msg = (
                "Business web research requires TAVILY_API_KEY and "
                "ENABLE_BUSINESS_RESEARCH=true in the API environment. Your "
                "question needs external industry or pricing sources."
            )
            if internal and len(internal.chunks) > 0:
                return internal.model_copy(
                    update={
                        "answer": f"{internal.answer}\n\n---\n\n*Note: {msg}*",
                        "intent": intent,
                        "used_external_search": False,
                    }
                )
            return RagQueryResponse(
                answer=msg,
                citations=internal.citations if internal else [],
                chunks=internal.chunks if internal else [],
                intent=intent,
                used_external_search=False,
                empty_reason="no_indexed_data",
            )

        if not used_external and (internal is None or len(internal.chunks) == 0):
            return RagQueryResponse(
                answer=(
                    "No external web results were returned for this business "
                    "research query. Try rephrasing or verify Tavily configuration."
                ),
                citations=[],
                chunks=[],
                external_results=[],
                intent=intent,
                used_external_search=False,
                empty_reason="no_indexed_data",
            )

        internal_blocks = (
            [
                {
                    "index": i + 1,
                    "content": c.content,
                    "label": self._chunk_label(c),
                }
                for i, c in enumerate(internal.chunks)
            ]
            if internal
            else []
        )

        external_blocks = [
            {
                "index": e.index,
                "content": e.content,
                "title": e.title,
                "url": e.url,
            }
            for e in external_chunks
        ]

        messages = [{"role": "system", "content": BUSINESS_RESEARCH_SYSTEM_PROMPT}]
        if chat_history:
            for msg in chat_history:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        messages.append({
            "role": "user",
            "content": build_hybrid_synthesis_user_prompt(
                query, internal_blocks, external_blocks
            ),
        })

        answer = await self._ai().chat(
            messages=messages,
            model=LLM_MODEL,
            temperature=0.2,
        )

        citations = [
            *(internal.citations if internal else []),
            *self._external_to_citations(external_chunks),
        ]

        return RagQueryResponse(
            answer=answer,
            citations=citations,
            chunks=internal.chunks if internal else [],
            external_results=external_chunks,
            intent=intent,
            used_external_search=used_external,
        )

    def _external_to_citations(
        self, chunks: list[ExternalResearchChunk]
    ) -> list[Citation]:
        return [
            Citation(
                index=e.index,
                source=VectorSource.external,
                title=e.title,
                snippet=e.content[:200],
                url=e.url,
            )
            for e in chunks
        ]

    def _chunk_label(self, chunk) -> str:  # noqa: ANN001 - RetrievedChunk
        m = chunk.metadata
        if m.source == VectorSource.outlook:
            return m.subject or "Email"
        return m.file_name or (
            m.source.value if isinstance(m.source, VectorSource) else str(m.source)
        )


# Backwards-compatible alias: the search module imports `QueryService`, while the
# query module's class is named QueryOrchestrationService (matching the NestJS
# QueryOrchestrationService). Same public surface (.query / .unified_search).
QueryService = QueryOrchestrationService
