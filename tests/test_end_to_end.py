"""Offline end-to-end: every stage of both engines, chained, with no services.

PDF -> page text -> agent chain -> coverage record -> knowledge chunks -> vectors
    -> scoped retrieval -> grounded answer with page citations
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from evidentia.ai.prompts import PromptRegistry
from evidentia.core.config import RetrievalSettings
from evidentia.modules.assistant.engine import AnswerEngine
from evidentia.modules.assistant.ranking import Candidate
from evidentia.modules.assistant.schemas import AskRequest
from evidentia.modules.auth.models import User, UserRole
from evidentia.modules.cards.scope import ScopedPolicy
from evidentia.modules.documents.text import PdfTextExtractor
from evidentia.modules.extraction.pipeline import ExtractionPipeline
from evidentia.modules.knowledge.chunker import build_knowledge_chunks
from evidentia.modules.knowledge.models import ChunkType
from tests.factories import make_pdf
from tests.fakes import PolicyReader, item


class InMemoryRetriever:
    """Cosine search over normalised vectors, same contract as the pgvector retriever."""

    def __init__(self, chunks: list[tuple[Candidate, list[float]]]) -> None:
        self.chunks = chunks

    async def semantic(
        self, version_ids: list[uuid.UUID], vector: list[float], *, per_policy: int
    ) -> list[Candidate]:
        scored = [
            replace(c, similarity=sum(a * b for a, b in zip(v, vector, strict=True)))
            for c, v in self.chunks
            if c.version_id in version_ids
        ]
        return sorted(scored, key=lambda c: -c.similarity)[:per_policy]

    async def published(self, version_ids: list[uuid.UUID]) -> list[Candidate]:
        return [c for c, _ in self.chunks if c.version_id in version_ids]


PAGES = {
    1: [item("Emergency medical expenses", 1, amount="EUR 1,000,000", currency="EUR")],
    2: [
        item(
            "Emergency dental treatment",
            2,
            amount="EUR 500",
            conditions="Pain relief only, treatment abroad",
        )
    ],
    3: [item("Baggage delay", 3, amount="EUR 300", conditions="Delay of more than 6 hours")],
}


async def test_policy_pdf_becomes_a_grounded_cited_answer() -> None:
    pdf = make_pdf(
        [
            "Section A Emergency medical expenses up to EUR 1,000,000",
            "Section B Emergency dental treatment up to EUR 500",
            "Section C Baggage delay EUR 300 after 6 hours",
        ]
    )
    pages = PdfTextExtractor().extract(pdf)
    reader = PolicyReader(PAGES)
    gateway, chat = reader.gateway()

    outcome = await ExtractionPipeline(gateway, PromptRegistry(environ={}), chunk_chars=80).run(
        pages, mode="full"
    )
    assert outcome.review_reasons == []
    record = outcome.record

    policy_id = version_id = uuid.uuid4()
    drafts = build_knowledge_chunks(policy_id, record, policy_label="Gold Cover")
    embedded = await gateway.embeddings.embed([draft.content for draft in drafts])
    retriever = InMemoryRetriever(
        [
            (
                Candidate(
                    chunk_id=uuid.uuid4(),
                    policy_id=policy_id,
                    version_id=version_id,
                    chunk_type=draft.chunk_type.value,
                    category=draft.category,
                    domain=draft.domain,
                    title=draft.title,
                    content=draft.content,
                    customer_facing=draft.customer_facing,
                    similarity=0.0,
                    ordinal=draft.ordinal,
                    fields=draft.fields,
                ),
                vector,
            )
            for draft, vector in zip(drafts, embedded.vectors, strict=True)
            if draft.chunk_type is ChunkType.COVERAGE
        ]
    )

    chat.on("answer", lambda req: "Yes. Gold Cover pays up to EUR 500 for emergency dental care.")
    scope = [ScopedPolicy(policy_id, version_id, "Gold Cover", uuid.uuid4(), "Example Gold")]

    async def resolve(user: User, card_id: uuid.UUID | None) -> list[ScopedPolicy]:
        return scope

    engine = AnswerEngine(
        gateway,
        PromptRegistry(environ={}),
        retriever,
        resolve,
        # Hash embeddings are a coarse stand-in, so this test uses lower floors.
        RetrievalSettings(similarity_floor=0.15, relaxed_floor=0.1),
    )
    user = User(
        id=uuid.uuid4(),
        email="e2e@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
        preferred_language="en",
        created_at=datetime.now(UTC),
    )
    response = await engine.ask(
        user, AskRequest(question="Am I covered at the dentist?", diagnostic=True)
    )

    assert response.status == "answered"
    assert response.citations[0].title == "Emergency Dental Treatment"
    assert response.citations[0].pages == [2]
    assert response.trace is not None
    assert response.trace.selected[0].category == "dental"
    answer_call = next(call for call in chat.calls if call.stage == "answer")
    assert "EUR 500" in answer_call.messages[-1].content  # the fact reached the model
    assert "Pain relief only" in answer_call.messages[-1].content
