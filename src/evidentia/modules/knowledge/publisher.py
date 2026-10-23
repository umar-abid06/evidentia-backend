"""Publishing lifecycle: pending -> processing -> ready (or failed, resumable).

A new version is built alongside the live one; the old version is archived only
once the new one is fully embedded, so customers never see a gap.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.ai.gateway import AIGateway
from evidentia.modules.catalog.models import Policy
from evidentia.modules.extraction.schemas import CoverageRecord
from evidentia.modules.knowledge.chunker import build_knowledge_chunks
from evidentia.modules.knowledge.models import KnowledgeChunk, PolicyVersion, VersionStatus

log = structlog.get_logger(__name__)


class IncompletePublishError(RuntimeError):
    pass


class PublishReport(BaseModel):
    version_id: uuid.UUID
    status: VersionStatus
    skipped: bool = False
    chunk_count: int = 0
    token_count: int = 0
    embedding_calls: int = 0
    duration_ms: int | None = None


class KnowledgePublisher:
    def __init__(
        self,
        session: AsyncSession,
        gateway: AIGateway,
        *,
        embedding_version: str,
        batch_size: int = 100,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.embedding_version = embedding_version
        self.batch_size = batch_size

    @property
    def embedding_model(self) -> str:
        return self.gateway.embeddings.model

    async def publish(self, version_id: uuid.UUID) -> PublishReport:
        version = await self.session.get(PolicyVersion, version_id, with_for_update=True)
        if version is None:
            raise LookupError(f"policy version {version_id} not found")
        if version.status in {VersionStatus.READY, VersionStatus.ARCHIVED}:
            await self.session.rollback()
            return PublishReport(version_id=version_id, status=version.status, skipped=True)

        version.status = VersionStatus.PROCESSING
        version.error = None
        await self.session.commit()
        started = time.perf_counter()
        try:
            await self._ensure_chunks(version)
            await self._embed_missing(version)
            await self._activate(version, started)
        except Exception as exc:
            await self.session.rollback()
            failed = await self.session.get(PolicyVersion, version_id, populate_existing=True)
            if failed is not None:
                failed.status = VersionStatus.FAILED
                failed.error = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                    "at": datetime.now(UTC).isoformat(),
                }
                await self.session.commit()
            log.exception("publish_failed", version_id=str(version_id))
            raise
        log.info(
            "publish_completed",
            version_id=str(version.id),
            chunks=version.chunk_count,
            tokens=version.token_count,
            embedding_calls=version.embedding_calls,
            duration_ms=version.publish_duration_ms,
        )
        return PublishReport(
            version_id=version.id,
            status=version.status,
            chunk_count=version.chunk_count,
            token_count=version.token_count,
            embedding_calls=version.embedding_calls,
            duration_ms=version.publish_duration_ms,
        )

    async def _ensure_chunks(self, version: PolicyVersion) -> None:
        """Idempotent: a resumed run keeps the chunks (and vectors) it already has."""
        policy = await self.session.get(Policy, version.policy_id)
        label = policy.display_name if policy else "Policy"
        drafts = build_knowledge_chunks(
            version.policy_id, CoverageRecord.model_validate(version.record), policy_label=label
        )
        existing = set(
            await self.session.scalars(
                select(KnowledgeChunk.chunk_key).where(
                    KnowledgeChunk.policy_version_id == version.id
                )
            )
        )
        self.session.add_all(
            KnowledgeChunk(
                policy_version_id=version.id,
                policy_id=version.policy_id,
                chunk_key=draft.chunk_key,
                ordinal=draft.ordinal,
                chunk_type=draft.chunk_type,
                category=draft.category,
                domain=draft.domain,
                title=draft.title,
                content=draft.content,
                fields=draft.fields,
                customer_facing=draft.customer_facing,
                token_count=draft.token_count,
                content_hash=draft.content_hash,
            )
            for draft in drafts
            if draft.chunk_key not in existing
        )
        version.chunk_count = len(drafts)
        version.token_count = sum(draft.token_count for draft in drafts)
        await self.session.commit()

    async def _embed_missing(self, version: PolicyVersion) -> None:
        stale = or_(
            KnowledgeChunk.embedding.is_(None),
            KnowledgeChunk.embedding_model.is_distinct_from(self.embedding_model),
            KnowledgeChunk.embedding_version.is_distinct_from(self.embedding_version),
        )
        while True:
            batch = list(
                await self.session.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.policy_version_id == version.id, stale)
                    .order_by(KnowledgeChunk.ordinal)
                    .limit(self.batch_size)
                )
            )
            if not batch:
                return
            result = await self.gateway.embed(
                stage="embed", texts=[chunk.content for chunk in batch]
            )
            for chunk, vector in zip(batch, result.vectors, strict=True):
                chunk.embedding = vector
                chunk.embedding_model = self.embedding_model
                chunk.embedding_version = self.embedding_version
            version.embedding_calls += 1
            await self.session.commit()  # resume point

    async def _activate(self, version: PolicyVersion, started: float) -> None:
        missing = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.policy_version_id == version.id,
                KnowledgeChunk.embedding.is_(None),
            )
        )
        if missing:
            raise IncompletePublishError(f"{missing} chunk(s) still lack vectors")
        await self.session.execute(
            update(PolicyVersion)
            .where(
                PolicyVersion.policy_id == version.policy_id,
                PolicyVersion.status == VersionStatus.READY,
                PolicyVersion.id != version.id,
            )
            .values(status=VersionStatus.ARCHIVED)
            .execution_options(synchronize_session=False)
        )
        version.status = VersionStatus.READY
        version.published_at = datetime.now(UTC)
        version.embedding_model = self.embedding_model
        version.embedding_version = self.embedding_version
        version.publish_duration_ms = int((time.perf_counter() - started) * 1000)
        await self.session.commit()
