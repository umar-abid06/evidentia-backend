"""Scoped pgvector retrieval. Search can only reach the versions it is given."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import Select, func, literal, select, true
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.modules.assistant.ranking import Candidate
from evidentia.modules.knowledge.models import ChunkType, KnowledgeChunk


class Retriever(Protocol):
    async def semantic(
        self, version_ids: list[uuid.UUID], vector: list[float], *, per_policy: int
    ) -> list[Candidate]: ...

    async def published(self, version_ids: list[uuid.UUID]) -> list[Candidate]: ...


def semantic_query(
    version_ids: list[uuid.UUID],
    vector: list[float],
    *,
    per_policy: int,
    embedding_model: str,
    embedding_version: str,
) -> Select[Any]:
    """One query: the nearest ``per_policy`` chunks of each version (LATERAL + HNSW).

    Only vectors stamped with the active embedding model and version are eligible,
    so vectors from different models can never be mixed.
    """
    versions = (
        select(
            func.unnest(literal(version_ids, type_=ARRAY(UUID(as_uuid=True)))).label("version_id")
        )
    ).subquery("versions")
    distance = KnowledgeChunk.embedding.cosine_distance(vector)
    hits = (
        select(
            KnowledgeChunk.id,
            KnowledgeChunk.policy_id,
            KnowledgeChunk.policy_version_id,
            KnowledgeChunk.chunk_type,
            KnowledgeChunk.category,
            KnowledgeChunk.domain,
            KnowledgeChunk.title,
            KnowledgeChunk.content,
            KnowledgeChunk.customer_facing,
            KnowledgeChunk.ordinal,
            KnowledgeChunk.fields,
            (1 - distance).label("similarity"),
        )
        .where(
            KnowledgeChunk.policy_version_id == versions.c.version_id,
            KnowledgeChunk.embedding.is_not(None),
            KnowledgeChunk.embedding_model == embedding_model,
            KnowledgeChunk.embedding_version == embedding_version,
        )
        .order_by(distance)
        .limit(per_policy)
        .lateral("hits")
    )
    return select(hits).select_from(versions).join(hits, true())


def _candidate(row: Any, similarity: float) -> Candidate:
    return Candidate(
        chunk_id=row.id,
        policy_id=row.policy_id,
        version_id=row.policy_version_id,
        chunk_type=str(row.chunk_type),
        category=row.category,
        domain=row.domain,
        title=row.title,
        content=row.content,
        customer_facing=row.customer_facing,
        similarity=similarity,
        ordinal=row.ordinal,
        fields=row.fields or {},
    )


class PgVectorRetriever:
    def __init__(
        self, session: AsyncSession, *, embedding_model: str, embedding_version: str
    ) -> None:
        self.session = session
        self.embedding_model = embedding_model
        self.embedding_version = embedding_version

    async def semantic(
        self, version_ids: list[uuid.UUID], vector: list[float], *, per_policy: int
    ) -> list[Candidate]:
        if not version_ids:
            return []
        query = semantic_query(
            version_ids,
            vector,
            per_policy=per_policy,
            embedding_model=self.embedding_model,
            embedding_version=self.embedding_version,
        )
        rows = (await self.session.execute(query)).all()
        return [_candidate(row, float(row.similarity)) for row in rows]

    async def published(self, version_ids: list[uuid.UUID]) -> list[Candidate]:
        if not version_ids:
            return []
        rows = await self.session.scalars(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.policy_version_id.in_(version_ids),
                KnowledgeChunk.chunk_type == ChunkType.COVERAGE,
            )
            .order_by(KnowledgeChunk.policy_version_id, KnowledgeChunk.ordinal)
        )
        return [
            Candidate(
                chunk_id=chunk.id,
                policy_id=chunk.policy_id,
                version_id=chunk.policy_version_id,
                chunk_type=chunk.chunk_type.value,
                category=chunk.category,
                domain=chunk.domain,
                title=chunk.title,
                content=chunk.content,
                customer_facing=chunk.customer_facing,
                similarity=1.0,
                ordinal=chunk.ordinal,
                fields=chunk.fields,
            )
            for chunk in rows
        ]
