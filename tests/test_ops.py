from __future__ import annotations

from httpx import AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from evidentia.ai.gateway import AIGateway
from evidentia.ai.providers.fake import HashEmbeddingModel, ScriptedChatModel
from evidentia.ai.telemetry import LLMCallRecord, LLMTelemetry
from evidentia.ai.types import ChatMessage
from evidentia.core.config import RetrievalSettings
from evidentia.core.metrics import prometheus_sink
from evidentia.modules.ops.models import LLMCall
from evidentia.modules.ops.service import BenchmarkQuestion, RetrievalBenchmark
from tests.test_assistant import GOLD, FakeRetriever, chunk


async def test_benchmark_reports_quality_and_cost_signals() -> None:
    retriever = FakeRetriever([chunk(GOLD, "dental", 0.82), chunk(GOLD, "emergency_medical", 0.61)])
    chat = ScriptedChatModel()
    gateway = AIGateway(reasoning=chat, fast=chat, embeddings=HashEmbeddingModel(dimensions=8))
    report = await RetrievalBenchmark(gateway, retriever, RetrievalSettings()).run(
        [GOLD],
        [
            BenchmarkQuestion(
                question="Am I covered at the dentist?", expected_categories=["dental"]
            ),
            BenchmarkQuestion(question="Tell me all my coverages"),
        ],
    )
    assert report.questions == 2
    assert report.no_match_rate == 0
    assert report.similarity["max"] == 0.82
    assert report.category_recall == 1.0
    assert report.rows[1].strategy == "summary"
    assert report.avg_token_load > 0
    assert [r.stage for r in gateway.telemetry.records] == ["benchmark"]


def test_prometheus_sink_counts_tokens_and_outcomes() -> None:
    labels = {"stage": "unit_stage", "model": "m1"}
    before = REGISTRY.get_sample_value("evidentia_llm_tokens_total", {**labels, "kind": "prompt"})
    prometheus_sink(
        LLMCallRecord(
            stage="unit_stage",
            model="m1",
            prompt_tokens=120,
            completion_tokens=30,
            latency_ms=850,
            payload_bytes=2048,
            retries=1,
            success=True,
        )
    )
    after = REGISTRY.get_sample_value("evidentia_llm_tokens_total", {**labels, "kind": "prompt"})
    assert (after or 0) - (before or 0) == 120
    assert REGISTRY.get_sample_value("evidentia_llm_retries_total", labels) == 1


async def test_metrics_endpoint_exposes_route_templates(client: AsyncClient) -> None:
    await client.get("/healthz")
    response = await client.get("/metrics/")
    assert response.status_code == 200
    assert 'evidentia_http_requests_total{method="GET",route="/healthz",status="200"}' in (
        response.text
    )


async def test_per_request_telemetry_does_not_leak_into_the_shared_gateway() -> None:
    chat = ScriptedChatModel({"answer": lambda _: "ok"})
    shared = AIGateway(
        reasoning=chat,
        fast=chat,
        embeddings=HashEmbeddingModel(dimensions=8),
        telemetry=LLMTelemetry(keep_records=False),
    )
    request_scoped = shared.with_telemetry(LLMTelemetry())
    await request_scoped.complete_text(stage="answer", messages=[ChatMessage("user", "hi")])
    assert len(request_scoped.telemetry.records) == 1
    assert shared.telemetry.records == []


def test_usage_aggregate_compiles_for_postgres() -> None:
    query = select(
        LLMCall.stage, func.percentile_cont(0.95).within_group(LLMCall.latency_ms)
    ).group_by(LLMCall.stage)
    sql = str(query.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert "percentile_cont" in sql
    assert "WITHIN GROUP (ORDER BY llm_calls.latency_ms)" in sql
