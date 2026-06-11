"""Query-module Pydantic schemas (port of the NestJS query module).

The query module has no HTTP controller of its own — it is consumed by the
``search`` and ``chat`` controllers. These models mirror the TypeScript
``OrchestratedQueryOptions`` and ``DocumentRequest`` shapes used internally by
``QueryOrchestrationService``. Response payloads (``RagQueryResponse`` /
``UnifiedSearchResponse``) live in :mod:`app.schema.common` and are reused.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schema.common import DictAccessModel, VectorSource

__all__ = (
    "DocumentRequest",
    "OrchestratedQueryOptions",
)


class OrchestratedQueryOptions(BaseModel):
    """Options accepted by ``QueryOrchestrationService.query`` / ``unifiedSearch``.

    Ported from the TS ``OrchestratedQueryOptions`` interface; camelCase aliases
    preserve the original JSON shape for any caller that serialises it.
    """

    model_config = ConfigDict(populate_by_name=True)

    workspace_id: str | None = Field(default=None, serialization_alias="workspaceId")
    project_id: str | None = Field(default=None, serialization_alias="projectId")
    sector_id: str | None = Field(default=None, serialization_alias="sectorId")
    sources: list[VectorSource] | None = None
    top_k: int | None = Field(default=None, serialization_alias="topK")
    force_external: bool | None = Field(
        default=None, serialization_alias="forceExternal"
    )
    chat_history: list[dict] | None = Field(
        default=None, serialization_alias="chatHistory"
    )


class DocumentRequest(DictAccessModel):
    """A parsed request to generate a document from a stored template.

    Ported from the TS ``DocumentRequest`` interface in ``query-intent.util.ts``.
    Subscriptable (``request["description"]``) because the documents service accesses
    it as a dict.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The text describing what to generate (trigger prefix stripped).
    description: str
    # True when triggered by the explicit ``/document`` slash command.
    explicit: bool
