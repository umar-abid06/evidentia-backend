"""Extraction chain: AI agents read and organise; code merges, checks and assembles.

discovery (parallel per chunk) -> merge & de-duplicate -> classify (batches)
-> organise (or deterministic plan) -> normalise & assemble -> review gate
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from evidentia.ai.concurrency import gather_limited
from evidentia.ai.gateway import AIGateway
from evidentia.ai.prompts import PromptRegistry
from evidentia.modules.documents.text import PageText
from evidentia.modules.extraction import prompts
from evidentia.modules.extraction.agents import (
    ClassificationResult,
    ClassifierAgent,
    DiscoveryAgent,
    OrganizerAgent,
)
from evidentia.modules.extraction.assembly import (
    apply_duplicate_links,
    assemble_record,
    deterministic_plan,
    merge_general,
    place_missing,
)
from evidentia.modules.extraction.chunking import ReadingChunk, build_reading_chunks
from evidentia.modules.extraction.merge import merge_items
from evidentia.modules.extraction.review import ReviewReason, review_gate
from evidentia.modules.extraction.schemas import Conflict, CoverageRecord, DiscoveryOutput

ExtractionMode = Literal["full", "amendment"]


@dataclass(slots=True)
class ExtractionOutcome:
    mode: ExtractionMode
    record: CoverageRecord
    conflicts: list[Conflict]
    review_reasons: list[ReviewReason]
    overall_confidence: float
    stats: dict[str, Any] = field(default_factory=dict)


class ExtractionPipeline:
    def __init__(
        self,
        gateway: AIGateway,
        registry: PromptRegistry,
        *,
        chunk_chars: int = 18_000,
        parallelism: int = 6,
        confidence_threshold: float = 0.80,
    ) -> None:
        self.gateway = gateway
        self.chunk_chars = chunk_chars
        self.parallelism = parallelism
        self.confidence_threshold = confidence_threshold
        self.discovery = DiscoveryAgent(gateway, registry)
        self.classifier = ClassifierAgent(gateway, registry, parallelism=parallelism)
        self.organizer = OrganizerAgent(gateway, registry)

    async def run(
        self, pages: list[PageText], *, mode: ExtractionMode, model: str | None = None
    ) -> ExtractionOutcome:
        sparse = mode == "amendment"
        chunks = build_reading_chunks(pages, max_chars=self.chunk_chars)

        def read(chunk: ReadingChunk) -> Callable[[], Awaitable[DiscoveryOutput]]:
            return lambda: self.discovery.read(chunk, sparse=sparse, model=model)

        outputs = await gather_limited([read(chunk) for chunk in chunks], limit=self.parallelism)
        found = [
            (chunk.index, item)
            for chunk, output in zip(chunks, outputs, strict=True)
            for item in output.items
        ]
        merged = merge_items(found)

        classification = (
            await self.classifier.classify(merged.items, model=model)
            if merged.items
            else ClassificationResult(by_item={}, unclassified=[])
        )
        items, linked = apply_duplicate_links(merged.items, classification.by_item)
        categories = {item.id: classification.by_item[item.id].category for item in items}
        unplaced = [item_id for item_id in classification.unclassified if item_id in categories]

        organizer_fallback = False
        if not sparse and items:
            organized = await self.organizer.organize(items, categories, model=model)
            if organized.plan is None:
                plan = deterministic_plan(items, categories)
                organizer_fallback = True
            else:
                plan = place_missing(organized.plan, organized.missing, categories)
                unplaced.extend(organized.missing)
        else:
            plan = deterministic_plan(items, categories)

        record = assemble_record(
            merge_general([output.general for output in outputs]),
            items,
            classification.by_item,
            plan,
        )
        confidence, reasons = review_gate(
            record,
            conflicts=merged.conflicts,
            unplaced=sorted(set(unplaced)),
            dropped=merged.dropped,
            threshold=self.confidence_threshold,
            sparse=sparse,
        )
        return ExtractionOutcome(
            mode=mode,
            record=record,
            conflicts=merged.conflicts,
            review_reasons=reasons,
            overall_confidence=confidence,
            stats={
                "pages": len(pages),
                "reading_chunks": len(chunks),
                "items_discovered": len(found),
                "items_after_merge": len(merged.items),
                "items_dropped": merged.dropped,
                "duplicates_linked": linked,
                "coverages": len(record.coverages),
                "organizer_fallback": organizer_fallback,
                "prompt_versions": {
                    spec.key: spec.version
                    for spec in (prompts.DISCOVERY, prompts.CLASSIFIER, prompts.ORGANIZER)
                },
            },
        )
