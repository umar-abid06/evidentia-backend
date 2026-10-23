"""Celery application. Start a worker with:

celery -A evidentia.workers.celery_app worker -Q documents,knowledge,generation,integrations
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging, worker_process_init

from evidentia.core.config import Settings, get_settings
from evidentia.core.logging import configure_logging
from evidentia.db.registry import load_models
from evidentia.workers.names import QUEUES, RECOVER_HEALTH_GUIDES

TASK_MODULES: list[str] = [
    "evidentia.workers.tasks.documents",
    "evidentia.workers.tasks.knowledge",
    "evidentia.workers.tasks.travel",
]


def create_celery(settings: Settings) -> Celery:
    app = Celery("evidentia", broker=settings.redis.broker_url or settings.redis.url)
    app.conf.update(
        include=TASK_MODULES,
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        worker_max_tasks_per_child=200,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": 3600},
        task_default_queue="default",
        task_routes={pattern: {"queue": queue} for pattern, queue in QUEUES.items()},
        task_soft_time_limit=settings.workers.soft_time_limit_s,
        task_time_limit=settings.workers.hard_time_limit_s,
        timezone="UTC",
        beat_schedule={
            "recover-health-guides": {
                "task": RECOVER_HEALTH_GUIDES,
                "schedule": float(settings.travel.recovery_interval_s),
            },
        },
    )
    return app


celery_app = create_celery(get_settings())


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)


@worker_process_init.connect
def _init_worker_process(**_: Any) -> None:
    load_models()
