"""Cost and quality operations: persisted call telemetry, usage reports, benchmarks."""

from __future__ import annotations

import statistics
import time
import uuid
from collections.abc import Iterable
from datetime import datetime

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.ai.gateway import AIGateway
from evidentia.ai.telemetry import LLMCallRecord, TelemetryReport, build_report
from evidentia.ai.tokens import count_tokens
from evidentia.core.config import RetrievalSettings
from evidentia.core.errors import NotFoundError
from evidentia.modules.assistant.concepts import topic_categories
from evidentia.modules.assistant.intent import classify
from evidentia.modules.assistant.ranking import (
    Selection,
    SelectionLimits,
    select_representatives,
    select_semantic,
)
from evidentia.modules.assistant.retrieval import Retriever
from evidentia.modules.documents.models import Document
from evidentia.modules.ops.models import LLMCall

log = structlog.get_logger(__name__)


def persist_calls(
    session: AsyncSession,
    records: Iterable[LLMCallRecord],
    *,
    document_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
) -> None:
    session.add_all(
        LLMCall(
            stage=record.stage,
            model=record.model,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            latency_ms=record.latency_ms,
            payload_bytes=record.payload_bytes,
            retries=record.retries,
            success=record.success,
            error_type=record.error_type,
            document_id=document_id,
            policy_version_id=version_id,
        )
        for record in records
    )


async def save_calls(
    session: AsyncSession,
    records: Iterable[LLMCallRecord],
    *,
    document_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
) -> None:
    """Best-effort telemetry write in its own session.

    Using a separate session means the caller's unit of work (and the objects it has
    loaded) is never rolled back or expired, and a failed caller cannot block it.
    """
    batch = list(records)
    if not batch:
        return
    try:
        async with AsyncSession(session.bind, expire_on_commit=False) as own:
            persist_calls(own, batch, document_id=document_id, version_id=version_id)
            await own.commit()
    except SQLAlchemyError:
        log.exception("llm_call_persist_failed", calls=len(batch))


class UsageRow(BaseModel):
    stage: str
    model: str
    calls: int
    failures: int
    retries: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms_avg: float
    latency_ms_p95: float


class OpsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def usage(
        self, *, since: datetime | None, until: datetime | None, stage: str | None
    ) -> list[UsageRow]:
        query = select(
            LLMCall.stage,
            LLMCall.model,
            func.count().label("calls"),
            func.sum(case((LLMCall.success.is_(False), 1), else_=0)).label("failures"),
            func.sum(LLMCall.retries).label("retries"),
            func.sum(LLMCall.prompt_tokens).label("prompt_tokens"),
            func.sum(LLMCall.completion_tokens).label("completion_tokens"),
            func.avg(LLMCall.latency_ms).label("latency_ms_avg"),
            func.percentile_cont(0.95).within_group(LLMCall.latency_ms).label("latency_ms_p95"),
        ).group_by(LLMCall.stage, LLMCall.model)
        if since:
            query = query.where(LLMCall.created_at >= since)
        if until:
            query = query.where(LLMCall.created_at < until)
        if stage:
            query = query.where(LLMCall.stage == stage)
        rows = (await self.session.execute(query.order_by(LLMCall.stage, LLMCall.model))).all()
        return [
            UsageRow(
                stage=row.stage,
                model=row.model,
                calls=row.calls,
                failures=row.failures or 0,
                retries=row.retries or 0,
                prompt_tokens=row.prompt_tokens or 0,
                completion_tokens=row.completion_tokens or 0,
                latency_ms_avg=round(float(row.latency_ms_avg or 0), 2),
                latency_ms_p95=round(float(row.latency_ms_p95 or 0), 2),
            )
            for row in rows
        ]

    async def document_report(self, document_id: uuid.UUID) -> TelemetryReport:
        """Tokens per page, the slowest call and the largest payload for one document."""
        document = await self.session.get(Document, document_id)
        if document is None:
            raise NotFoundError("Document not found.", code="document_not_found")
        calls = await self.session.scalars(
            select(LLMCall).where(LLMCall.document_id == document_id).order_by(LLMCall.created_at)
        )
        records = [
            LLMCallRecord(
                stage=call.stage,
                model=call.model,
                prompt_tokens=call.prompt_tokens,
                completion_tokens=call.completion_tokens,
                latency_ms=call.latency_ms,
                payload_bytes=call.payload_bytes,
                retries=call.retries,
                success=call.success,
                error_type=call.error_type,
                created_at=call.created_at,
            )
            for call in calls
        ]
        return build_report(records, pages=document.page_count)


class BenchmarkQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    expected_categories: list[str] = Field(default_factory=list)


class BenchmarkRow(BaseModel):
    question: str
    intent: str
    strategy: str
    matched: bool
    top_similarity: float | None
    floor_applied: float | None
    selected_categories: list[str]
    category_recall: float | None
    token_load: int
    latency_ms: float


class BenchmarkReport(BaseModel):
    questions: int
    no_match_rate: float
    similarity: dict[str, float]
    category_recall: float | None
    avg_token_load: float
    latency_ms_p50: float
    latency_ms_p95: float
    rows: list[BenchmarkRow]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(q * (len(ordered) - 1)))
    return round(ordered[index], 4)


class RetrievalBenchmark:
    """Retrieval-only evaluation: no answer generation, so it is cheap and repeatable."""

    def __init__(
        self, gateway: AIGateway, retriever: Retriever, settings: RetrievalSettings
    ) -> None:
        self.gateway = gateway
        self.retriever = retriever
        self.settings = settings
        self.limits = SelectionLimits(
            floor=settings.similarity_floor,
            relaxed_floor=settings.relaxed_floor,
            per_policy=settings.max_chunks_per_policy,
            total=settings.max_chunks_total,
        )

    async def run(
        self, version_ids: list[uuid.UUID], questions: list[BenchmarkQuestion]
    ) -> BenchmarkReport:
        rows = [await self._one(version_ids, item) for item in questions]
        tops = [row.top_similarity for row in rows if row.top_similarity is not None]
        recalls = [row.category_recall for row in rows if row.category_recall is not None]
        latencies = [row.latency_ms for row in rows]
        return BenchmarkReport(
            questions=len(rows),
            no_match_rate=round(sum(not row.matched for row in rows) / len(rows), 4) if rows else 0,
            similarity={
                "min": round(min(tops), 4) if tops else 0.0,
                "p50": _percentile(tops, 0.5),
                "p90": _percentile(tops, 0.9),
                "max": round(max(tops), 4) if tops else 0.0,
            },
            category_recall=round(statistics.fmean(recalls), 4) if recalls else None,
            avg_token_load=round(statistics.fmean(r.token_load for r in rows), 1) if rows else 0,
            latency_ms_p50=_percentile(latencies, 0.5),
            latency_ms_p95=_percentile(latencies, 0.95),
            rows=rows,
        )

    async def _one(self, version_ids: list[uuid.UUID], item: BenchmarkQuestion) -> BenchmarkRow:
        started = time.perf_counter()
        intent = classify(item.question)
        selection: Selection
        if intent.needs_vector_search:
            embedded = await self.gateway.embed(stage="benchmark", texts=[item.question])
            candidates = await self.retriever.semantic(
                version_ids, embedded.vectors[0], per_policy=self.settings.candidates_per_policy
            )
            selection = select_semantic(
                candidates,
                intent=intent.kind,
                topic_categories=topic_categories(intent.topics),
                topics_detected=bool(intent.topics),
                limits=self.limits,
            )
            strategy = "semantic"
        else:
            selection = select_representatives(
                await self.retriever.published(version_ids),
                limits=self.limits,
                customer_facing_only=intent.kind == "compare_general",
            )
            strategy = "summary"
        categories = sorted({chunk.category for chunk in selection.chunks})
        expected = set(item.expected_categories)
        return BenchmarkRow(
            question=item.question,
            intent=intent.kind,
            strategy=strategy,
            matched=bool(selection.chunks),
            top_similarity=max((c.similarity for c in selection.chunks), default=None)
            if strategy == "semantic"
            else None,
            floor_applied=selection.floor_applied,
            selected_categories=categories,
            category_recall=round(len(expected & set(categories)) / len(expected), 4)
            if expected
            else None,
            token_load=sum(count_tokens(chunk.content) for chunk in selection.chunks),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
