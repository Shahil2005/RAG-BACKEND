"""RAG module schemas.

The RAG module has no HTTP controller in the NestJS source (``rag.module.ts``
exposes no controller) — ``RagService`` is consumed by the ``query``, ``search``
and ``chat`` modules. So there are no request DTOs here; this module reuses the
shared response/vector types from :mod:`app.schema.common`.

:class:`RagQueryOptions` mirrors the optional ``options`` argument of the NestJS
``RagService.query(ctx, query, options?)`` method (an internal call shape, not a
request body). :class:`LiveContextResult` mirrors the live Graph context shape
returned by the Outlook/SharePoint helper services.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from app.schema.common import RetrievedChunk, VectorSource

__all__ = (
    "LiveContextBlock",
    "LiveContextResult",
    "RagQueryOptions",
)


@dataclass
class RagQueryOptions:
    """Internal options for :meth:`app.services.rag_service.RagService.query`.

    Mirrors the optional ``options`` object the NestJS callers pass; not a
    request body (the RAG module has no controller).
    """

    workspace_id: str | None = None
    project_id: str | None = None
    sector_id: str | None = None
    sources: list[VectorSource] | None = None
    top_k: int | None = None
    bypass_topic_guard: bool = False
    # Restrict retrieval to this project's uploaded files only.
    project_only: bool = False


class LiveContextBlock(BaseModel):
    """One numbered context block built from live Microsoft Graph data."""

    model_config = ConfigDict(populate_by_name=True)

    index: int
    content: str
    label: str


@dataclass
class LiveContextResult:
    """Result of building live mail/document context (blocks + retrieved chunks)."""

    blocks: list[LiveContextBlock] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)
