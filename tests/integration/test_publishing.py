from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from evidentia.ai.errors import ProviderUnavailableError
from evidentia.ai.gateway import AIGateway
from evidentia.ai.providers.fake import HashEmbeddingModel, ScriptedChatModel
from evidentia.ai.types import EmbeddingResult
from evidentia.core.tasks import RecordingDispatcher
from evidentia.db.session import Database
from evidentia.modules.catalog.models import Policy
from evidentia.modules.knowledge.models import (
    KnowledgeChunk,
    PolicyChange,
    PolicyVersion,
    VersionStatus,
)
from evidentia.modules.knowledge.publisher import KnowledgePublisher
from evidentia.modules.knowledge.service import KnowledgeService
from evidentia.workers.names import PUBLISH_POLICY_VERSION
from tests.test_knowledge import sample_record

pytestmark = pytest.mark.integration


def _gateway(embeddings: HashEmbeddingModel | None = None) -> AIGateway:
    chat = ScriptedChatModel()
    return AIGateway(
        reasoning=chat, fast=chat, embeddings=embeddings or HashEmbeddingModel(), retry_delays=()
    )


async def _policy(db: Database) -> uuid.UUID:
    async with db.session() as session:
        policy = Policy(
            code="gold", name="Gold", display_name="Gold Cover", insurer_name="Example SE"
        )
        session.add(policy)
        await session.commit()
        return policy.id


async def test_versions_publish_swap_and_skip_unchanged_content(
    db: Database, dispatcher: RecordingDispatcher
) -> None:
    policy_id = await _policy(db)
    async with db.session() as session:
        service = KnowledgeService(session)
        v1 = await service.stage_version(
            policy_id, sample_record(), document_id=None, actor_id=None
        )
        await session.commit()
        assert v1 is not None
        assert dispatcher.names() == [PUBLISH_POLICY_VERSION]

        report = await KnowledgePublisher(session, _gateway(), embedding_version="1").publish(v1.id)
        assert report.status is VersionStatus.READY
        missing = await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.policy_version_id == v1.id, KnowledgeChunk.embedding.is_(None))
        )
        assert missing == 0

        unchanged = await service.stage_version(
            policy_id, sample_record(), document_id=None, actor_id=None
        )
        assert unchanged is None

        v2 = await service.stage_version(
            policy_id, sample_record(delay_amount=450), document_id=None, actor_id=None
        )
        await session.commit()
        assert v2 is not None
        await KnowledgePublisher(session, _gateway(), embedding_version="1").publish(v2.id)

        statuses = dict(
            (
                await session.execute(select(PolicyVersion.version_number, PolicyVersion.status))
            ).all()
        )
        assert statuses == {1: VersionStatus.ARCHIVED, 2: VersionStatus.READY}
        changes = (
            await session.execute(
                select(
                    PolicyChange.field_path, PolicyChange.old_value, PolicyChange.new_value
                ).where(PolicyChange.policy_version_id == v2.id)
            )
        ).all()
        assert [tuple(row) for row in changes] == [("coverages.cov_b.amount", 300, 450)]


async def test_failed_publish_resumes_where_it_stopped(db: Database) -> None:
    policy_id = await _policy(db)

    class FlakyEmbeddings(HashEmbeddingModel):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def embed(self, texts: list[str]) -> EmbeddingResult:  # type: ignore[override]
            self.calls += 1
            if self.calls == 2:
                raise ProviderUnavailableError("provider down")
            return await super().embed(texts)

    async with db.session() as session:
        version = await KnowledgeService(session).stage_version(
            policy_id, sample_record(), document_id=None, actor_id=None
        )
        await session.commit()
        assert version is not None
        flaky = FlakyEmbeddings()
        publisher = KnowledgePublisher(
            session, _gateway(flaky), embedding_version="1", batch_size=2
        )
        with pytest.raises(ProviderUnavailableError):
            await publisher.publish(version.id)
        failed = await session.get(PolicyVersion, version.id, populate_existing=True)
        assert failed is not None
        assert failed.status is VersionStatus.FAILED

        report = await publisher.publish(version.id)
        assert report.status is VersionStatus.READY
        assert report.embedding_calls >= 2
