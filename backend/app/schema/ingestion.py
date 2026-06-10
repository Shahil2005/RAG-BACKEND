"""Ingestion-related Pydantic schemas.

The response shapes returned by the NestJS IngestionController/IngestionService
already live in :mod:`app.schema.common` (they were shared `@starbot/types`).
They are re-exported here so callers can ``from app.schema.ingestion import ...``
and the exact camelCase JSON the controller returned is preserved.

This module additionally declares the ``WorkerSyncTarget`` row shape used by the
worker-targets endpoint and the internal ``ExtractResult`` used by
``DocumentTextService`` (port of the NestJS ``ExtractResult`` interface).
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.schema.common import (
    DocumentSourceSyncResult,
    DocumentsSyncResult,
    IngestionStatus,
    IngestionVerification,
    OutlookSyncResult,
)

__all__ = (
    "DocumentSourceSyncResult",
    "DocumentsSyncResult",
    "ExtractResult",
    "IngestionStatus",
    "IngestionVerification",
    "OutlookSyncResult",
    "WorkerSyncTarget",
)


class WorkerSyncTarget(BaseModel):
    """A single user/org row returned by ``GET /ingestion/worker/targets``.

    Mirrors the NestJS ``listWorkerSyncTargets`` output:
    ``{ userId, organizationId, role }``.
    """

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(serialization_alias="userId")
    organization_id: str = Field(serialization_alias="organizationId")
    role: str


@dataclass
class ExtractResult:
    """Result of resolving document text into chunks.

    Port of the NestJS ``ExtractResult`` interface in ``document-text.service.ts``.
    """

    chunks: list[str] = field(default_factory=list)
    reason: str = ""
