from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from evidentia.ai.telemetry import TelemetryReport
from evidentia.api.deps import AIGatewayDep, SessionDep, SettingsDep
from evidentia.core.errors import NotFoundError
from evidentia.modules.assistant.retrieval import PgVectorRetriever
from evidentia.modules.auth.dependencies import AdminUser, StaffUser
from evidentia.modules.knowledge.service import KnowledgeService
from evidentia.modules.ops.service import (
    BenchmarkQuestion,
    BenchmarkReport,
    OpsService,
    RetrievalBenchmark,
    UsageRow,
)


def get_ops_service(session: SessionDep) -> OpsService:
    return OpsService(session)


OpsDep = Annotated[OpsService, Depends(get_ops_service)]

router = APIRouter(prefix="/admin", tags=["admin: operations"])


class BenchmarkRequest(BaseModel):
    policy_id: uuid.UUID
    questions: list[BenchmarkQuestion] = Field(min_length=1, max_length=100)


@router.get("/ops/llm-usage", response_model=list[UsageRow])
async def llm_usage(
    _: StaffUser,
    service: OpsDep,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    stage: Annotated[str | None, Query(max_length=40)] = None,
) -> list[UsageRow]:
    """Calls, failures, retries, tokens and latency per stage and model."""
    return await service.usage(since=since, until=until, stage=stage)


@router.get("/documents/{document_id}/telemetry", response_model=TelemetryReport)
async def document_telemetry(
    document_id: uuid.UUID, _: StaffUser, service: OpsDep
) -> TelemetryReport:
    """Tokens per page, slowest call and largest payload for one document."""
    return await service.document_report(document_id)


@router.post("/ops/retrieval-benchmark", response_model=BenchmarkReport)
async def retrieval_benchmark(
    payload: BenchmarkRequest,
    _: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    ai: AIGatewayDep,
) -> BenchmarkReport:
    """No-match rate, similarity distribution, category recall, token load and latency."""
    live = await KnowledgeService(session).live_version(payload.policy_id)
    if live is None:
        raise NotFoundError("The policy has no published version.", code="version_not_found")
    retriever = PgVectorRetriever(
        session,
        embedding_model=ai.embeddings.model,
        embedding_version=settings.llm.embedding_version,
    )
    benchmark = RetrievalBenchmark(ai, retriever, settings.retrieval)
    return await benchmark.run([live.id], payload.questions)
