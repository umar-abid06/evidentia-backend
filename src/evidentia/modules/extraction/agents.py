"""The three reading agents. Each has one job, one JSON contract, and a
deterministic checkpoint that validates its output before the next hand-off."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from evidentia.ai.concurrency import gather_limited
from evidentia.ai.gateway import AIGateway
from evidentia.ai.prompts import PromptRegistry, compose_messages
from evidentia.ai.structured import validate_contract
from evidentia.modules.extraction import prompts
from evidentia.modules.extraction.chunking import ReadingChunk
from evidentia.modules.extraction.merge import MergedItem
from evidentia.modules.extraction.schemas import (
    Classification,
    ClassificationOutput,
    DiscoveryOutput,
    OrganizerOutput,
)
from evidentia.modules.extraction.taxonomy import CATEGORIES, UNCLASSIFIED

CLASSIFY_BATCH = 60
CLASSIFY_CONTEXT_CHARS = 240
ORGANIZE_CONTEXT_CHARS = 320

log = structlog.get_logger(__name__)


def _compact(item: MergedItem, limit: int, *, category: str | None = None) -> str:
    draft = item.draft
    parts = [
        str(draft.title.value or ""),
        str(draft.item_type.value or ""),
        " ".join(str(v) for v in (draft.amount.value, draft.currency.value) if v),
        str(draft.description.value or ""),
    ]
    text = " | ".join(part for part in parts if part)
    prefix = f"{item.id} [{category}] " if category else f"{item.id} "
    return (prefix + text)[:limit]


class DiscoveryAgent:
    def __init__(self, gateway: AIGateway, registry: PromptRegistry) -> None:
        self.gateway = gateway
        self.registry = registry

    async def read(
        self, chunk: ReadingChunk, *, sparse: bool, model: str | None
    ) -> DiscoveryOutput:
        prompt = await self.registry.resolve(prompts.DISCOVERY)
        contract = prompts.DISCOVERY_CONTRACT + (prompts.SPARSE_MODE if sparse else "")
        data = await self.gateway.complete_json(
            stage="discovery",
            messages=compose_messages(prompt, contract=contract, user_content=chunk.text),
            model=model,
        )
        output = validate_contract(DiscoveryOutput, data, stage="discovery")
        # Checkpoint: evidence must point at pages this chunk actually contains.
        allowed = set(chunk.pages)
        for item in output.items:
            item.evidence = [ref for ref in item.evidence if ref.page in allowed]
        return output


@dataclass(slots=True)
class ClassificationResult:
    by_item: dict[str, Classification]
    unclassified: list[str]


class ClassifierAgent:
    def __init__(self, gateway: AIGateway, registry: PromptRegistry, *, parallelism: int) -> None:
        self.gateway = gateway
        self.registry = registry
        self.parallelism = parallelism

    async def classify(self, items: list[MergedItem], *, model: str | None) -> ClassificationResult:
        prompt = await self.registry.resolve(prompts.CLASSIFIER)
        contract = prompts.classifier_contract()
        batches = [items[i : i + CLASSIFY_BATCH] for i in range(0, len(items), CLASSIFY_BATCH)]

        def job(batch: list[MergedItem]) -> Callable[[], Awaitable[list[Classification]]]:
            async def run() -> list[Classification]:
                payload = "\n".join(_compact(item, CLASSIFY_CONTEXT_CHARS) for item in batch)
                data = await self.gateway.complete_json(
                    stage="classify",
                    messages=compose_messages(prompt, contract=contract, user_content=payload),
                    model=model,
                )
                return validate_contract(
                    ClassificationOutput, data, stage="classify"
                ).classifications

            return run

        results = await gather_limited([job(batch) for batch in batches], limit=self.parallelism)

        # Checkpoint: known ids only, taxonomy keys only, first answer wins.
        known = {item.id for item in items}
        by_item: dict[str, Classification] = {}
        for classification in (c for batch in results for c in batch):
            if classification.item_id not in known or classification.item_id in by_item:
                continue
            if classification.category not in CATEGORIES:
                classification.category = UNCLASSIFIED
            if classification.duplicate_of not in known - {classification.item_id}:
                classification.duplicate_of = None
            by_item[classification.item_id] = classification
        unclassified = [
            item.id
            for item in items
            if item.id not in by_item or by_item[item.id].category == UNCLASSIFIED
        ]
        for item_id in unclassified:
            by_item.setdefault(item_id, Classification(item_id=item_id, category=UNCLASSIFIED))
        return ClassificationResult(by_item=by_item, unclassified=unclassified)


@dataclass(slots=True)
class OrganizerResult:
    plan: OrganizerOutput | None
    missing: list[str]
    failed: bool


class OrganizerAgent:
    def __init__(self, gateway: AIGateway, registry: PromptRegistry) -> None:
        self.gateway = gateway
        self.registry = registry

    async def organize(
        self, items: list[MergedItem], categories: dict[str, str], *, model: str | None
    ) -> OrganizerResult:
        """Returns the plan, or ``failed=True`` so the deterministic plan takes over."""
        prompt = await self.registry.resolve(prompts.ORGANIZER)
        payload = "\n".join(
            _compact(item, ORGANIZE_CONTEXT_CHARS, category=categories[item.id]) for item in items
        )
        try:
            data = await self.gateway.complete_json(
                stage="organize",
                messages=compose_messages(
                    prompt, contract=prompts.ORGANIZER_CONTRACT, user_content=payload
                ),
                model=model,
            )
            plan = validate_contract(OrganizerOutput, data, stage="organize")
        except Exception as exc:  # noqa: BLE001 - any failure falls back deterministically
            log.warning("organizer_fallback", error=type(exc).__name__)
            return OrganizerResult(plan=None, missing=[], failed=True)

        # Checkpoint: every item exactly once, no invented ids.
        placed = Counter(
            item_id
            for domain in plan.domains
            for subdomain in domain.subdomains
            for item_id in subdomain.item_ids
        )
        known = {item.id for item in items}
        if any(count > 1 for count in placed.values()) or set(placed) - known:
            log.warning(
                "organizer_plan_rejected",
                duplicates=[k for k, v in placed.items() if v > 1][:10],
                unknown=json.dumps(sorted(set(placed) - known)[:10]),
            )
            return OrganizerResult(plan=None, missing=[], failed=True)
        return OrganizerResult(plan=plan, missing=sorted(known - set(placed)), failed=False)
