"""Search request DTOs — port of the NestJS SearchController body validators.

The response shapes (``UnifiedSearchResponse``, ``ExternalResearchChunk``, ...) are
shared and live in ``app.schema.common``; this module only declares the request
bodies that were defined inline in ``search.controller.ts``.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schema.common import UnifiedSearchResponse, VectorSource

__all__ = (
    "InternetSearchDto",
    "UnifiedSearchDto",
    "UnifiedSearchResponse",
)


class UnifiedSearchDto(BaseModel):
    """Body for POST /search/unified (was UnifiedSearchDto / UnifiedSearchRequest)."""

    model_config = ConfigDict(populate_by_name=True)

    query: str
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    sources: list[VectorSource] | None = None
    top_k: int | None = Field(default=None, alias="topK")


class InternetSearchDto(BaseModel):
    """Body for POST /search/internet (was InternetSearchDto)."""

    model_config = ConfigDict(populate_by_name=True)

    query: str
