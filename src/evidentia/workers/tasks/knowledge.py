from __future__ import annotations

import uuid
from typing import Any

from celery import Task
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from evidentia.ai.errors import AIError
from evidentia.ai.factory import build_gateway
from evidentia.ai.prompts import PromptRegistry
from evidentia.core.i18n import Language
from evidentia.modules.catalog.models import Policy
from evidentia.modules.extraction.schemas import CoverageRecord
from evidentia.modules.knowledge.models import CoverageTranslation, PolicyVersion
from evidentia.modules.knowledge.publisher import KnowledgePublisher
from evidentia.modules.knowledge.translation import CoverageTranslator, customer_view, view_hash
from evidentia.workers.celery_app import celery_app
from evidentia.workers.names import PUBLISH_POLICY_VERSION, TRANSLATE_COVERAGE
from evidentia.workers.runtime import WorkerContext, run_job

RETRY_COUNTDOWNS = (30, 60, 120)


async def _publish(ctx: WorkerContext, version_id: uuid.UUID) -> None:
    async with ctx.db.session() as session:
        publisher = KnowledgePublisher(
            session,
            build_gateway(ctx.settings.llm),
            embedding_version=ctx.settings.llm.embedding_version,
            batch_size=ctx.settings.knowledge.embed_batch_size,
        )
        await publisher.publish(version_id)


@celery_app.task(name=PUBLISH_POLICY_VERSION, bind=True, acks_late=True, max_retries=3)
def publish_policy_version(self: Task[Any, Any], version_id: str) -> None:
    """Partial runs resume where they stopped, so a retry only embeds what is missing."""
    try:
        run_job(lambda ctx: _publish(ctx, uuid.UUID(version_id)), version_id=version_id)
    except AIError as exc:
        if not exc.transient or self.request.retries >= len(RETRY_COUNTDOWNS):
            raise
        raise self.retry(exc=exc, countdown=RETRY_COUNTDOWNS[self.request.retries]) from exc


async def _translate(ctx: WorkerContext, version_id: uuid.UUID, language: Language) -> None:
    async with ctx.db.session() as session:
        version = await session.get(PolicyVersion, version_id)
        if version is None:
            return
        policy = await session.scalar(select(Policy).where(Policy.id == version.policy_id))
        label = policy.display_name if policy else "Policy"
        view = customer_view(CoverageRecord.model_validate(version.record), policy_label=label)
        settings = ctx.settings.knowledge
        translator = CoverageTranslator(
            build_gateway(ctx.settings.llm),
            PromptRegistry(),
            batch_tokens=settings.translation_batch_tokens,
            parallelism=settings.translation_parallelism,
            retries=settings.translation_retries,
        )
        protected = [label] + ([policy.insurer_name] if policy else [])
        payload = await translator.translate(view, language, protected=protected)
        statement = insert(CoverageTranslation).values(
            policy_version_id=version_id,
            language=language,
            payload=payload,
            source_hash=view_hash(view),
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["policy_version_id", "language"],
                set_={
                    "payload": statement.excluded.payload,
                    "source_hash": statement.excluded.source_hash,
                },
            )
        )
        await session.commit()


@celery_app.task(name=TRANSLATE_COVERAGE, acks_late=True)
def translate_coverage(version_id: str, language: Language) -> None:
    run_job(
        lambda ctx: _translate(ctx, uuid.UUID(version_id), language),
        version_id=version_id,
        language=language,
    )
