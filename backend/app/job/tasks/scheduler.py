"""Scheduler Celery task.

Ported from the BullMQ `starbot-scheduler` worker in `apps/worker/src/index.ts`.

The NestJS worker added a repeating `tick` job (`repeat: { every: SYNC_INTERVAL_MS }`)
on the `starbot-scheduler` queue. Each tick called `enqueueScheduledSyncs`, which:
  1. fetched the list of sync targets (users with Microsoft tokens), and
  2. enqueued an `outlook` and a `sharepoint` ingestion job per target.

Here that repeat is expressed as a Celery beat schedule (see app/job/schedules.py) and
the per-target enqueue fans out to the `ingestion.sync` Celery task.
"""

import asyncio

from celery import shared_task
from sqlalchemy import select

from app import logger
from app.core.celery import get_celery_db_session
from app.core.settings import settings


async def _list_sync_targets() -> list[dict[str, str]]:
    """Return users with Microsoft tokens for scheduled sync.

    ORM translation of `IngestionService.listWorkerSyncTargets`:

        SELECT om.user_id, om.organization_id, om.role::text AS role
        FROM organization_members om
        INNER JOIN oauth_tokens ot ON ot.user_id = om.user_id
        ORDER BY om.organization_id, om.user_id
    """
    from app.models.organization import OrganizationMember
    from app.models.user import OAuthToken

    async with get_celery_db_session() as session:
        stmt = (
            select(
                OrganizationMember.user_id,
                OrganizationMember.organization_id,
                OrganizationMember.role,
            )
            .join(OAuthToken, OAuthToken.user_id == OrganizationMember.user_id)
            .order_by(
                OrganizationMember.organization_id,
                OrganizationMember.user_id,
            )
        )
        rows = (await session.execute(stmt)).all()

    return [
        {
            "userId": str(row.user_id),
            "organizationId": str(row.organization_id),
            "role": row.role.value if hasattr(row.role, "value") else str(row.role),
        }
        for row in rows
    ]


@shared_task(name="scheduler.tick")  # type: ignore[misc]
def scheduler_tick() -> dict[str, int]:
    """Enqueue scheduled ingestion syncs for every sync target.

    Equivalent to the `starbot-scheduler` worker's `tick` handler ->
    `enqueueScheduledSyncs`. Gated by `WORKER_SERVICE_TOKEN` exactly like the original
    worker (which skipped all work when the token was unset).

    Returns:
        {"targets": N} — the number of users a sync was enqueued for.

    """
    if not settings.worker_service_token:
        logger.info("scheduler.tick skipped: WORKER_SERVICE_TOKEN not set")
        return {"targets": 0}

    # Local import to avoid importing the task module at scheduler-task definition time.
    from app.job.tasks.ingestion import sync_ingestion

    targets = asyncio.run(_list_sync_targets())
    logger.info("[scheduler] enqueueing sync for %d user(s)", len(targets))

    for target in targets:
        # Matches the two enqueues per target in enqueueScheduledSyncs (outlook + sharepoint).
        for source in ("outlook", "sharepoint"):
            sync_ingestion.delay(
                source,
                target["userId"],
                target["organizationId"],
            )

    return {"targets": len(targets)}
