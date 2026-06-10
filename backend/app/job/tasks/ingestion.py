"""Ingestion Celery tasks.

Ported from the BullMQ `starbot-ingestion` worker in `apps/worker/src/index.ts`.

In the NestJS monorepo the worker process consumed jobs off the `starbot-ingestion`
queue and, for each job, made an authenticated HTTP call back to the API
(`POST /api/v1/ingestion/outlook/sync` or `.../documents/sync`). Now that the API and
the worker live in the same FastAPI backend, the task calls the ported
``IngestionService`` directly (lazy import) instead of looping back over HTTP.

Job payload (BullMQ `IngestionJobPayload`):
    { source: "outlook" | "sharepoint", userId, organizationId }
"""

import asyncio
from typing import Any

from celery import shared_task

from app import logger
from app.core.celery import get_celery_db_session


async def _run_sync(source: str, user_id: str, organization_id: str) -> dict[str, Any]:
    """Run the actual ingestion sync against the ported IngestionService.

    Mirrors `triggerIngestion` in apps/worker/src/index.ts: an `outlook` source maps
    to the Outlook sync, anything else (e.g. `sharepoint`) maps to the documents sync.
    The original passed `x-user-id` / `x-organization-id` / `x-role: owner` headers; we
    build the equivalent auth context for the service call.
    """
    # Lazy import to avoid the rag/ingestion/query circular-import cycle at boot.
    async with get_celery_db_session() as session:
        try:
            from app.services.ingestion_service import IngestionService
        except ImportError:
            # TODO(migration): the ingestion module has not been ported yet. Wire this
            # task to IngestionService.sync_outlook / sync_all_documents once
            # app/services/ingestion_service.py exists. The auth context the original
            # worker forwarded was {userId, organizationId, role: "owner"}.
            logger.warning(
                "IngestionService not yet ported; skipping %s sync for user=%s org=%s",
                source,
                user_id,
                organization_id,
            )
            return {
                "skipped": True,
                "reason": "ingestion_service_not_ported",
                "source": source,
            }

        ctx = {
            "userId": user_id,
            "organizationId": organization_id,
            "role": "owner",
        }
        ingestion = IngestionService(session)
        if source == "outlook":
            return await ingestion.sync_outlook(ctx)
        return await ingestion.sync_all_documents(ctx)


@shared_task(name="ingestion.sync")  # type: ignore[misc]
def sync_ingestion(source: str, user_id: str, organization_id: str) -> dict[str, Any]:
    """Process a single ingestion sync job (BullMQ `starbot-ingestion` -> `sync`).

    Args:
        source: "outlook" or "sharepoint" (any non-outlook value -> documents sync).
        user_id: target user id.
        organization_id: target organization id.

    Returns:
        The ingestion result dict (or a skip marker if the service is unavailable).

    """
    logger.info(
        "Processing ingestion job source=%s user=%s org=%s",
        source,
        user_id,
        organization_id,
    )
    return asyncio.run(_run_sync(source, user_id, organization_id))
