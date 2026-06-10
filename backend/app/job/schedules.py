from celery import Celery
from celery.schedules import schedule

from app.core.settings import settings


def register_celery_schedules(celery_app: Celery) -> None:
    # Convert the BullMQ `repeat: { every: SYNC_INTERVAL_MS }` (ms) into seconds for
    # Celery beat. Mirrors apps/worker `starbot-scheduler-tick` (default: every 6h).
    sync_interval_sec = max(1, settings.ingestion_sync_interval_ms // 1000)

    celery_app.conf.beat_schedule = {
        # Ported from apps/worker `starbot-scheduler` repeating `tick` job.
        "ingestion_scheduler_tick": {
            "task": "scheduler.tick",
            "schedule": schedule(sync_interval_sec),
        },
    }
