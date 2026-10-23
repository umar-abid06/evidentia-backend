from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from celery import Task
from redis.asyncio import Redis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.ai.errors import AIError
from evidentia.core.i18n import as_language
from evidentia.core.tasks import TaskCall, enqueue
from evidentia.modules.admin.store import prompt_registry, resolve_gateway
from evidentia.modules.knowledge.translation import CoverageTranslator
from evidentia.modules.travel.generation import (
    GUIDE_TRANSLATABLE_KEYS,
    HEALTH_GUIDE,
    generate_health_guide,
    generate_jetlag_plan,
)
from evidentia.modules.travel.jetlag import JetLagMetrics
from evidentia.modules.travel.models import GenerationStatus, HealthGuide, JetLagPlan
from evidentia.workers.celery_app import celery_app
from evidentia.workers.names import (
    GENERATE_HEALTH_GUIDE,
    GENERATE_JETLAG_PLAN,
    RECOVER_HEALTH_GUIDES,
)
from evidentia.workers.runtime import WorkerContext, run_job

RETRY_COUNTDOWNS = (60, 120)

log = structlog.get_logger(__name__)


class SourceGuideNotReadyError(AIError):
    """The English source is still generating; translation must wait."""

    transient = True


def _retry(task: Task[Any, Any], exc: Exception) -> None:
    if task.request.retries >= len(RETRY_COUNTDOWNS):
        raise exc
    raise task.retry(exc=exc, countdown=RETRY_COUNTDOWNS[task.request.retries]) from exc


async def _generate_guide(ctx: WorkerContext, guide_id: uuid.UUID) -> None:
    redis = Redis.from_url(ctx.settings.redis.url, decode_responses=True)
    try:
        async with ctx.db.session() as session:
            guide = await session.get(HealthGuide, guide_id)
            if guide is None or guide.status is GenerationStatus.READY:
                return
            # One generator per destination and language at a time.
            lock = redis.lock(
                f"lock:health-guide:{guide.country_code}:{guide.region_code}:{guide.language}",
                timeout=ctx.settings.travel.stale_generation_minutes * 60,
                blocking=False,
            )
            if not await lock.acquire():
                log.info("health_guide_locked", guide_id=str(guide_id))
                return
            try:
                guide.status = GenerationStatus.GENERATING
                guide.claimed_at = datetime.now(UTC)
                guide.attempts += 1
                await session.commit()
                await _fill_guide(ctx, session, guide)
            except Exception as exc:
                await session.rollback()
                failed = await session.get(HealthGuide, guide_id, populate_existing=True)
                if failed is not None:
                    failed.status = GenerationStatus.FAILED
                    failed.error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
                    await session.commit()
                raise
            finally:
                await lock.release()
    finally:
        await redis.aclose()


async def _fill_guide(ctx: WorkerContext, session: AsyncSession, guide: HealthGuide) -> None:
    gateway = await resolve_gateway(session, ctx.settings)
    registry = prompt_registry(session)
    if guide.language == "en":
        content, prompt_version = await generate_health_guide(
            gateway, registry, country=guide.country_code, region=guide.region_code
        )
        payload = content.model_dump(mode="json")
    else:
        source = await session.scalar(
            select(HealthGuide).where(
                HealthGuide.country_code == guide.country_code,
                HealthGuide.region_code == guide.region_code,
                HealthGuide.language == "en",
                HealthGuide.version == guide.version,
                HealthGuide.status == GenerationStatus.READY,
            )
        )
        if source is None or source.content is None:
            raise SourceGuideNotReadyError("English guide is not ready yet.")
        translator = CoverageTranslator(gateway, registry, keys=GUIDE_TRANSLATABLE_KEYS)
        payload = await translator.translate(
            source.content, as_language(guide.language), protected=[]
        )
        prompt_version = HEALTH_GUIDE.version
    now = datetime.now(UTC)
    guide.content = payload
    guide.model = gateway.model_for("fast")
    guide.prompt_version = prompt_version
    guide.status = GenerationStatus.READY
    guide.generated_at = now
    guide.expires_at = now + timedelta(days=ctx.settings.travel.guide_ttl_days)
    guide.error = None
    await session.commit()


@celery_app.task(name=GENERATE_HEALTH_GUIDE, bind=True, acks_late=True, max_retries=2)
def generate_health_guide_task(self: Task[Any, Any], guide_id: str) -> None:
    try:
        run_job(lambda ctx: _generate_guide(ctx, uuid.UUID(guide_id)), guide_id=guide_id)
    except AIError as exc:
        _retry(self, exc)


async def _recover(ctx: WorkerContext) -> int:
    """Re-queues guides abandoned by a crashed worker."""
    cutoff = datetime.now(UTC) - timedelta(minutes=ctx.settings.travel.stale_generation_minutes)
    async with ctx.db.session() as session:
        stale = list(
            await session.scalars(
                select(HealthGuide.id).where(
                    or_(
                        (HealthGuide.status == GenerationStatus.GENERATING)
                        & (HealthGuide.claimed_at < cutoff),
                        (HealthGuide.status == GenerationStatus.PENDING)
                        & (HealthGuide.created_at < cutoff),
                    )
                )
            )
        )
    for guide_id in stale:
        enqueue(TaskCall(GENERATE_HEALTH_GUIDE, (str(guide_id),)))
    return len(stale)


@celery_app.task(name=RECOVER_HEALTH_GUIDES)
def recover_health_guides() -> int:
    return run_job(_recover)


async def _generate_plan(ctx: WorkerContext, plan_id: uuid.UUID) -> None:
    async with ctx.db.session() as session:
        plan = await session.get(JetLagPlan, plan_id)
        if plan is None or plan.status is GenerationStatus.READY:
            return
        plan.status = GenerationStatus.GENERATING
        plan.attempts += 1
        await session.commit()
        gateway = await resolve_gateway(session, ctx.settings)
        try:
            content = await generate_jetlag_plan(
                gateway, prompt_registry(session), JetLagMetrics.model_validate(plan.metrics)
            )
        except Exception as exc:
            plan.status = GenerationStatus.FAILED
            plan.error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
            await session.commit()
            raise
        # Saved only after it passed schema validation.
        plan.plan = content.model_dump(mode="json")
        plan.model = gateway.model_for("fast")
        plan.status = GenerationStatus.READY
        plan.error = None
        await session.commit()


@celery_app.task(name=GENERATE_JETLAG_PLAN, bind=True, acks_late=True, max_retries=2)
def generate_jetlag_plan_task(self: Task[Any, Any], plan_id: str) -> None:
    try:
        run_job(lambda ctx: _generate_plan(ctx, uuid.UUID(plan_id)), plan_id=plan_id)
    except AIError as exc:
        _retry(self, exc)
