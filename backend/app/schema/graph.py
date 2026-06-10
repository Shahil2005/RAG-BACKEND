"""Microsoft Graph schemas (port of the NestJS graph DTOs + service interfaces).

Request validation mirrors `graph-sync.dto.ts`; the controller response shapes
(`{synced: true}` / `{connected: bool}`) and the service-level Graph data shapes
(`GraphEmail`, `GraphDriveItem`, `GraphSite`, `GraphDrive`) are preserved exactly
so the frontend and the sibling ingestion/mail/documents modules keep working.
"""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.schema.common import DictAccessModel

__all__ = (
    "CrawlLimits",
    "DeltaCursorStore",
    "DocumentSource",
    "DownloadedFile",
    "GraphDrive",
    "GraphDriveItem",
    "GraphEmail",
    "GraphSite",
    "GraphSyncRequest",
    "StatusResponse",
    "SyncResponse",
    "WalkDriveOptions",
)

# 'sharepoint' | 'onedrive' (was DocumentSource in graph.service.ts).
DocumentSource = Literal["sharepoint", "onedrive"]


# DictAccessModel (dict-style access for these Graph shapes) lives in app.schema.common.


# ---------------------------------------------------------------------------
# Controller request / response shapes
# ---------------------------------------------------------------------------


class GraphSyncRequest(BaseModel):
    """Body of POST /graph/sync (port of GraphSyncDto).

    `providerAccessToken` is required and non-empty; `providerRefreshToken` is
    optional and may be null.
    """

    model_config = ConfigDict(populate_by_name=True)

    provider_access_token: str = Field(
        validation_alias="providerAccessToken",
        serialization_alias="providerAccessToken",
        min_length=1,
    )
    provider_refresh_token: str | None = Field(
        default=None,
        validation_alias="providerRefreshToken",
        serialization_alias="providerRefreshToken",
    )


class SyncResponse(BaseModel):
    synced: bool = True


class StatusResponse(BaseModel):
    connected: bool


# ---------------------------------------------------------------------------
# Service-level Graph data shapes
# ---------------------------------------------------------------------------


class GraphEmail(DictAccessModel):
    """A single Outlook message normalized for the mail/ingestion modules."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    subject: str
    sender: str
    received_at: str = Field(serialization_alias="receivedAt")
    body: str
    conversation_id: str | None = Field(default=None, serialization_alias="conversationId")
    is_read: bool = Field(serialization_alias="isRead")


class GraphDriveItem(DictAccessModel):
    """A SharePoint/OneDrive file (or folder) discovered while crawling drives."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    web_url: str | None = Field(default=None, serialization_alias="webUrl")
    last_modified: str | None = Field(default=None, serialization_alias="lastModified")
    drive_id: str = Field(serialization_alias="driveId")
    site_id: str | None = Field(default=None, serialization_alias="siteId")
    site_name: str | None = Field(default=None, serialization_alias="siteName")
    mime_type: str | None = Field(default=None, serialization_alias="mimeType")
    size: int | None = None
    is_folder: bool = Field(serialization_alias="isFolder")
    parent_path: str | None = Field(default=None, serialization_alias="parentPath")
    source: DocumentSource


class GraphSite(DictAccessModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str = Field(serialization_alias="displayName")
    web_url: str | None = Field(default=None, serialization_alias="webUrl")


class GraphDrive(DictAccessModel):
    id: str
    name: str


class WalkDriveOptions(BaseModel):
    max_depth: int
    max_files: int


class CrawlLimits(BaseModel):
    """Crawl limits (was WalkDriveOptions & { maxSites; maxFileBytes })."""

    model_config = ConfigDict(populate_by_name=True)

    max_sites: int = Field(serialization_alias="maxSites")
    max_files: int = Field(serialization_alias="maxFiles")
    max_depth: int = Field(serialization_alias="maxDepth")
    max_file_bytes: int = Field(serialization_alias="maxFileBytes")


class DownloadedFile(DictAccessModel):
    """Raw bytes of a downloaded drive item plus its resolved content type."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    buffer: bytes
    content_type: str


class DeltaCursorStore(Protocol):
    """Persists Graph delta links for incremental OneDrive sync.

    The concrete implementation lives in the ingestion module; the graph service
    only depends on this structural interface (was DeltaCursorStore in TS).
    """

    async def get_delta_link(
        self, ctx: "object", source: str, drive_id: str
    ) -> str | None: ...

    async def set_delta_link(
        self, ctx: "object", source: str, drive_id: str, delta_link: str | None
    ) -> None: ...
