"""Per-call model telemetry: tokens, latency, payload size and retries, by stage."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel

log = structlog.get_logger("evidentia.llm")


@dataclass(frozen=True, slots=True)
class LLMCallRecord:
    stage: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    payload_bytes: int
    retries: int
    success: bool
    error_type: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


TelemetrySink = Callable[[LLMCallRecord], None]


class StageUsage(BaseModel):
    stage: str
    calls: int = 0
    failures: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms_total: float = 0.0
    latency_ms_max: float = 0.0


class CallRef(BaseModel):
    stage: str
    model: str
    latency_ms: float
    payload_bytes: int


class TelemetryReport(BaseModel):
    calls: int
    failures: int
    retries: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tokens_per_page: float | None
    stages: list[StageUsage]
    slowest_call: CallRef | None
    largest_payload: CallRef | None


def build_report(records: Iterable[LLMCallRecord], *, pages: int | None = None) -> TelemetryReport:
    items = list(records)
    stages: dict[str, StageUsage] = defaultdict(lambda: StageUsage(stage=""))
    for rec in items:
        usage = stages[rec.stage]
        usage.stage = rec.stage
        usage.calls += 1
        usage.failures += 0 if rec.success else 1
        usage.retries += rec.retries
        usage.prompt_tokens += rec.prompt_tokens
        usage.completion_tokens += rec.completion_tokens
        usage.total_tokens += rec.total_tokens
        usage.latency_ms_total += rec.latency_ms
        usage.latency_ms_max = max(usage.latency_ms_max, rec.latency_ms)

    def ref(rec: LLMCallRecord | None) -> CallRef | None:
        if rec is None:
            return None
        return CallRef(
            stage=rec.stage,
            model=rec.model,
            latency_ms=rec.latency_ms,
            payload_bytes=rec.payload_bytes,
        )

    total = sum(rec.total_tokens for rec in items)
    return TelemetryReport(
        calls=len(items),
        failures=sum(not rec.success for rec in items),
        retries=sum(rec.retries for rec in items),
        prompt_tokens=sum(rec.prompt_tokens for rec in items),
        completion_tokens=sum(rec.completion_tokens for rec in items),
        total_tokens=total,
        tokens_per_page=round(total / pages, 2) if pages else None,
        stages=sorted(stages.values(), key=lambda usage: usage.stage),
        slowest_call=ref(max(items, key=lambda rec: rec.latency_ms, default=None)),
        largest_payload=ref(max(items, key=lambda rec: rec.payload_bytes, default=None)),
    )


class LLMTelemetry:
    """Collects call records for one unit of work and fans them out to sinks."""

    def __init__(self, sinks: Sequence[TelemetrySink] = (), *, keep_records: bool = True) -> None:
        self.records: list[LLMCallRecord] = []
        self._sinks = list(sinks)
        self._keep = keep_records

    def add_sink(self, sink: TelemetrySink) -> None:
        self._sinks.append(sink)

    def record(self, record: LLMCallRecord) -> None:
        if self._keep:
            self.records.append(record)
        payload = asdict(record)
        payload.pop("created_at")
        log.info("llm_call", **payload)
        for sink in self._sinks:
            try:
                sink(record)
            except Exception:  # telemetry must never break a call
                log.exception("telemetry_sink_failed", sink=getattr(sink, "__name__", repr(sink)))

    def report(self, *, pages: int | None = None) -> TelemetryReport:
        return build_report(self.records, pages=pages)
