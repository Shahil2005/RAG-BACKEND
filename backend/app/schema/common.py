"""Shared workspace types ported from `@starbot/types`.

These mirror the TypeScript interfaces/enums/unions used across the RAG,
ingestion, query, mail and document modules. Response field aliases preserve the
exact camelCase JSON shape returned by the original NestJS API so the frontend
keeps working unchanged.

`AuthContext` and `MemberRole` already live in `app.schema.auth` /
`app.models.organization`; they are re-exported here for convenience instead of
being redefined.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.organization import MemberRole
from app.schema.auth import AuthContext


class DictAccessModel(BaseModel):
    """Pydantic base that ALSO supports dict-style access: ``obj["key"]`` and
    ``obj.get("key", default)``.

    Several ported callers treat these models as dicts, using either the snake_case
    field name or the camelCase serialization alias. This resolves both so those
    call sites work without rewriting each one.
    """

    def _resolve(self, key: str):
        fields = type(self).model_fields
        if key in fields:
            return getattr(self, key)
        for name, field in fields.items():
            if key in (field.serialization_alias, field.alias):
                return getattr(self, name)
        raise KeyError(key)

    def __getitem__(self, key: str):
        return self._resolve(key)

    def get(self, key: str, default=None):
        try:
            return self._resolve(key)
        except KeyError:
            return default

__all__ = (
    # re-exported (defined elsewhere)
    "MemberRole",
    "AuthContext",
    "DictAccessModel",
    # enums / unions
    "VectorSource",
    "QueryIntent",
    "DocumentTemplateType",
    "EmailCategory",
    "EmptyReason",
    "IngestionSource",
    "ChatRole",
    # models
    "TemplateVariable",
    "DocumentTemplate",
    "DocumentDraft",
    "VectorMetadata",
    "Citation",
    "RetrievedChunk",
    "UnifiedSearchRequest",
    "ProjectSummary",
    "ProjectSector",
    "ProjectFileSummary",
    "ProjectDetail",
    "ExternalResearchChunk",
    "UnifiedSearchResponse",
    "GenerateDocumentRequest",
    "MailClassificationResult",
    "MailClassificationEmailMetadata",
    "MailClassificationRow",
    "IngestionJobPayload",
    "EmbedRequest",
    "EmbedResponse",
    "RagQueryResponse",
    "ChatSessionSummary",
    "ChatMessageRecord",
    "OutlookSyncResult",
    "DocumentSourceSyncResult",
    "DocumentsSyncResult",
    "IngestionVerification",
    "IngestionStatus",
)


# ---------------------------------------------------------------------------
# Enums / unions
# ---------------------------------------------------------------------------


class VectorSource(str, Enum):
    """Origin namespace for an indexed vector.

    The original TS union also allowed a ``"${string}-workspace"`` template
    literal (see :func:`app.core.utils.workspace_partition`). That dynamic form
    is not enumerable; pass such values as plain strings where a free-form
    source string is accepted.
    """

    outlook = "outlook"
    sharepoint = "sharepoint"
    onedrive = "onedrive"
    project = "project"
    external = "external"


class QueryIntent(str, Enum):
    off_topic = "off_topic"
    m365_only = "m365_only"
    business_research = "business_research"
    hybrid = "hybrid"
    document_generation = "document_generation"


class DocumentTemplateType(str, Enum):
    estimate = "estimate"
    job_summary = "job_summary"
    report = "report"
    quotation = "quotation"
    customer_email = "customer_email"


class EmailCategory(str, Enum):
    important = "important"
    spam = "spam"
    closed = "closed"
    pending_action = "pending_action"
    sent = "sent"


# Free-standing literal unions (kept as ``Literal`` to match the TS string unions).
EmptyReason = Literal["out_of_scope", "no_indexed_data", "not_connected"]
IngestionSource = Literal["outlook", "sharepoint", "onedrive", "workspace"]
ChatRole = Literal["user", "assistant", "system"]


# ---------------------------------------------------------------------------
# Templates & document generation
# ---------------------------------------------------------------------------


class TemplateVariable(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    label: str
    required: bool | None = None
    example: str | None = None


class DocumentTemplate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    organization_id: str
    workspace_id: str | None = None
    name: str
    type: DocumentTemplateType
    content: str
    variables: list[TemplateVariable]
    is_default: bool | None = None
    created_at: str
    updated_at: str


class DocumentDraft(BaseModel):
    """Marker attached to an assistant chat message when the bot generated a document.

    Lets the chat UI offer follow-up actions (e.g. "Save to Outlook drafts" for
    an emitted customer email).
    """

    model_config = ConfigDict(populate_by_name=True)

    type: DocumentTemplateType
    template_name: str = Field(serialization_alias="templateName")
    # Subject line for customer_email drafts.
    subject: str | None = None
    # The generated document body (markdown / plain text).
    body: str
    # True when this draft can be saved straight to the user's Outlook Drafts.
    can_save_to_outlook: bool | None = Field(default=None, serialization_alias="canSaveToOutlook")


# ---------------------------------------------------------------------------
# Vectors / retrieval
# ---------------------------------------------------------------------------


class VectorMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: VectorSource
    organization_id: str = Field(serialization_alias="organizationId")
    workspace_id: str | None = Field(default=None, serialization_alias="workspaceId")
    project_id: str | None = Field(default=None, serialization_alias="projectId")
    sector_id: str | None = Field(default=None, serialization_alias="sectorId")
    email_id: str | None = Field(default=None, serialization_alias="emailId")
    file_id: str | None = Field(default=None, serialization_alias="fileId")
    file_name: str | None = Field(default=None, serialization_alias="fileName")
    sender: str | None = None
    timestamp: str | None = None
    chunk_id: str = Field(serialization_alias="chunkId")
    subject: str | None = None
    web_url: str | None = Field(default=None, serialization_alias="webUrl")


class Citation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    index: int
    source: VectorSource
    title: str
    snippet: str
    url: str | None = None
    timestamp: str | None = None


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    content: str
    score: float
    metadata: VectorMetadata


# ---------------------------------------------------------------------------
# Search / projects
# ---------------------------------------------------------------------------


class UnifiedSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str
    workspace_id: str | None = Field(default=None, serialization_alias="workspaceId")
    project_id: str | None = Field(default=None, serialization_alias="projectId")
    sources: list[VectorSource] | None = None
    top_k: int | None = Field(default=None, serialization_alias="topK")


class ProjectSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str | None = None
    custom_instructions: str | None = None
    created_at: str
    updated_at: str


class ProjectSector(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    project_id: str
    name: str
    created_at: str


class ProjectFileSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    project_id: str
    file_name: str
    mime_type: str | None = None
    size_bytes: int | None = None
    is_indexed: bool
    indexed_at: str | None = None
    index_reason: str | None = None
    chunk_count: int
    sector_id: str | None = None
    created_at: str


class ProjectDetail(ProjectSummary):
    files: list[ProjectFileSummary]
    sectors: list[ProjectSector]


class ExternalResearchChunk(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    index: int
    title: str
    content: str
    url: str
    source: Literal["external"] = "external"


class UnifiedSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    answer: str
    citations: list[Citation]
    internal_results: list[RetrievedChunk] = Field(serialization_alias="internalResults")
    external_results: list[ExternalResearchChunk] | None = Field(
        default=None, serialization_alias="externalResults"
    )
    intent: QueryIntent | None = None
    used_external_search: bool | None = Field(
        default=None, serialization_alias="usedExternalSearch"
    )


class GenerateDocumentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    template_id: str = Field(serialization_alias="templateId")
    variables: dict[str, str]
    workspace_id: str | None = Field(default=None, serialization_alias="workspaceId")


# ---------------------------------------------------------------------------
# Mail classification
# ---------------------------------------------------------------------------


class MailClassificationResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email_id: str = Field(serialization_alias="emailId")
    graph_message_id: str = Field(serialization_alias="graphMessageId")
    subject: str
    category: EmailCategory
    confidence: float
    reasoning: str | None = None


class MailClassificationEmailMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    subject: str
    sender: str
    received_at: str


class MailClassificationRow(BaseModel):
    """Row returned by GET /mail/classifications."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    category: EmailCategory
    confidence: float
    reasoning: str | None = None
    created_at: str
    email_metadata: MailClassificationEmailMetadata


# ---------------------------------------------------------------------------
# Ingestion / embeddings
# ---------------------------------------------------------------------------


class IngestionJobPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: IngestionSource
    user_id: str = Field(serialization_alias="userId")
    organization_id: str = Field(serialization_alias="organizationId")
    workspace_id: str | None = Field(default=None, serialization_alias="workspaceId")
    since: str | None = None


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model: str
    dimensions: int


# ---------------------------------------------------------------------------
# RAG query response
# ---------------------------------------------------------------------------


class RagQueryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    answer: str
    citations: list[Citation]
    chunks: list[RetrievedChunk]
    external_results: list[ExternalResearchChunk] | None = Field(
        default=None, serialization_alias="externalResults"
    )
    intent: QueryIntent | None = None
    used_external_search: bool | None = Field(
        default=None, serialization_alias="usedExternalSearch"
    )
    scope_reason: str | None = Field(default=None, serialization_alias="scopeReason")
    empty_reason: EmptyReason | None = Field(default=None, serialization_alias="emptyReason")
    # Present when the bot generated a document from a stored template.
    document_draft: DocumentDraft | None = Field(
        default=None, serialization_alias="documentDraft"
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatSessionSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    created_at: str
    updated_at: str


class ChatMessageRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    session_id: str
    role: ChatRole
    content: str
    citations: list[Citation] | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Sync results / ingestion status
# ---------------------------------------------------------------------------


class OutlookSyncResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    indexed: int
    skipped: int
    vectors_upserted: int = Field(serialization_alias="vectorsUpserted")
    emails_fetched: int = Field(serialization_alias="emailsFetched")
    namespace: str
    pinecone_index_name: str = Field(serialization_alias="pineconeIndexName")


class DocumentSourceSyncResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    indexed: int
    skipped: int
    vectors_upserted: int = Field(serialization_alias="vectorsUpserted")
    files_fetched: int = Field(serialization_alias="filesFetched")
    namespace: str
    pinecone_index_name: str = Field(serialization_alias="pineconeIndexName")


class DocumentsSyncResult(BaseModel):
    sharepoint: DocumentSourceSyncResult
    onedrive: DocumentSourceSyncResult


class IngestionVerification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    check_namespace_in_pinecone_console: str = Field(
        serialization_alias="checkNamespaceInPineconeConsole"
    )
    expected_dimensions: int = Field(serialization_alias="expectedDimensions")


class IngestionStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    organization_id: str = Field(serialization_alias="organizationId")
    pinecone_index_name: str = Field(serialization_alias="pineconeIndexName")
    pinecone_namespace: str = Field(serialization_alias="pineconeNamespace")
    pinecone_vector_count: int | None = Field(
        default=None, serialization_alias="pineconeVectorCount"
    )
    email_metadata_count: int = Field(serialization_alias="emailMetadataCount")
    indexed_email_count: int = Field(serialization_alias="indexedEmailCount")
    file_metadata_count: int = Field(serialization_alias="fileMetadataCount")
    indexed_file_count: int = Field(serialization_alias="indexedFileCount")
    last_outlook_sync_at: str | None = Field(
        default=None, serialization_alias="lastOutlookSyncAt"
    )
    last_documents_sync_at: str | None = Field(
        default=None, serialization_alias="lastDocumentsSyncAt"
    )
    last_outlook_sync_error: str | None = Field(
        default=None, serialization_alias="lastOutlookSyncError"
    )
    last_documents_sync_error: str | None = Field(
        default=None, serialization_alias="lastDocumentsSyncError"
    )
    metadata_only_file_count: int = Field(serialization_alias="metadataOnlyFileCount")
    ai_service_reachable: bool = Field(serialization_alias="aiServiceReachable")
    # True when WORKER_SERVICE_TOKEN is configured on the API.
    scheduled_sync_enabled: bool = Field(serialization_alias="scheduledSyncEnabled")
    # Milliseconds between worker scheduler ticks (INGESTION_SYNC_INTERVAL_MS).
    scheduled_sync_interval_ms: int = Field(serialization_alias="scheduledSyncIntervalMs")
    # Estimated next worker sync from last successful sync + interval (null if never synced).
    estimated_next_scheduled_sync_at: str | None = Field(
        default=None, serialization_alias="estimatedNextScheduledSyncAt"
    )
    verification: IngestionVerification
