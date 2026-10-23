from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from evidentia.ai.gateway import AIGateway
from evidentia.ai.prompts import PromptRegistry
from evidentia.ai.providers.fake import HashEmbeddingModel, ScriptedChatModel
from evidentia.ai.types import ChatRequest
from evidentia.modules.extraction.schemas import (
    CoverageEntry,
    CoverageRecord,
    DomainEntry,
    EvidencedValue,
    EvidenceRef,
    PlanSubdomain,
)
from evidentia.modules.knowledge.chunker import build_knowledge_chunks, record_hash
from evidentia.modules.knowledge.models import ChunkType
from evidentia.modules.knowledge.translation import (
    CoverageTranslator,
    TranslationDriftError,
    apply_leaves,
    collect_leaves,
    customer_view,
    token_batches,
)

POLICY_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")


def _value(value: Any, page: int = 2) -> EvidencedValue:
    return EvidencedValue(
        value=value, confidence=0.9, evidence=[EvidenceRef(page=page, quote=str(value))]
    )


def sample_record(delay_amount: float = 300) -> CoverageRecord:
    return CoverageRecord(
        general={"insurer_name": _value("Example Insurance SE", 1)},
        domains=[
            DomainEntry(
                key="medical",
                name="Medical",
                summary="Emergency treatment abroad.",
                subdomains=[
                    PlanSubdomain(key="emergency_medical", name="Medical", item_ids=["cov_m"])
                ],
            ),
            DomainEntry(
                key="baggage",
                name="Baggage & Belongings",
                subdomains=[
                    PlanSubdomain(key="baggage_delay", name="Delay", item_ids=["cov_b"]),
                    PlanSubdomain(key="general_conditions", name="Terms", item_ids=["cov_t"]),
                ],
            ),
        ],
        coverages=[
            CoverageEntry(
                id="cov_m",
                category="emergency_medical",
                domain="medical",
                canonical_name="Emergency Medical Expenses",
                title="Emergency medical expenses",
                amount=1_000_000,
                currency="EUR",
                confidence=0.95,
                evidence=[EvidenceRef(page=2, quote="medical")],
                fields={"conditions": _value("Treatment must be medically necessary")},
            ),
            CoverageEntry(
                id="cov_b",
                category="baggage_delay",
                domain="baggage",
                canonical_name="Baggage Delay",
                title="Baggage delay",
                amount=delay_amount,
                currency="EUR",
                confidence=0.95,
                evidence=[EvidenceRef(page=5, quote="baggage")],
                fields={"conditions": _value("Delay of more than 6 hours", 5)},
            ),
            CoverageEntry(
                id="cov_t",
                category="general_conditions",
                domain="terms",
                canonical_name="Definitions",
                title="Definitions",
                customer_facing=False,
                confidence=0.95,
                evidence=[EvidenceRef(page=9, quote="definitions")],
                fields={},
            ),
        ],
    )


def test_one_chunk_per_fact_with_stable_keys() -> None:
    first = build_knowledge_chunks(POLICY_ID, sample_record(), policy_label="Gold Cover")
    second = build_knowledge_chunks(POLICY_ID, sample_record(), policy_label="Gold Cover")
    assert first == second
    coverage = [chunk for chunk in first if chunk.chunk_type is ChunkType.COVERAGE]
    assert [chunk.title for chunk in coverage] == [
        "Emergency Medical Expenses",
        "Baggage Delay",
        "Definitions",
    ]
    delay = coverage[1]
    assert "Amount: EUR 300" in delay.content
    assert "Conditions: Delay of more than 6 hours" in delay.content
    assert delay.fields["pages"] == [5]
    assert not coverage[2].customer_facing
    assert {chunk.chunk_type for chunk in first} == {ChunkType.OVERVIEW, ChunkType.COVERAGE}
    assert len({chunk.chunk_key for chunk in first}) == len(first)


def test_record_hash_detects_content_changes_only() -> None:
    assert record_hash(sample_record()) == record_hash(sample_record())
    assert record_hash(sample_record()) != record_hash(sample_record(delay_amount=400))


def test_customer_view_hides_internal_items() -> None:
    view = customer_view(sample_record(), policy_label="Gold Cover")
    names = [item["name"] for domain in view["domains"] for item in domain["items"]]
    assert names == ["Emergency Medical Expenses", "Baggage Delay"]
    assert view["domains"][0]["items"][0]["amount"] == "EUR 1,000,000"


def test_only_free_text_leaves_are_translated() -> None:
    view = customer_view(sample_record(), policy_label="Gold Cover")
    leaves = collect_leaves(view)
    assert "domains.0.items.0.name" in leaves
    assert not any(path.endswith((".amount", ".currency", ".id", ".key")) for path in leaves)
    assert "policy" not in leaves
    rebuilt = apply_leaves(view, {"domains.0.name": "Medizin"})
    assert rebuilt["domains"][0]["name"] == "Medizin"
    assert view["domains"][0]["name"] == "Medical"


def test_batches_respect_the_token_budget() -> None:
    leaves = {f"k{i}": "word " * 40 for i in range(10)}
    batches = token_batches(leaves, budget=120)
    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) == 10


def _translator(responder: Any) -> tuple[CoverageTranslator, ScriptedChatModel]:
    chat = ScriptedChatModel({"translate": responder})
    gateway = AIGateway(reasoning=chat, fast=chat, embeddings=HashEmbeddingModel(dimensions=8))
    return CoverageTranslator(gateway, PromptRegistry(environ={}), batch_tokens=200), chat


async def test_translator_keeps_structure_and_protected_values() -> None:
    def german(request: ChatRequest) -> dict[str, Any]:
        batch = json.loads(request.messages[-1].content)
        return {"translations": {key: f"DE:{value}" for key, value in batch.items()}}

    translator, _ = _translator(german)
    view = customer_view(sample_record(), policy_label="Gold Cover")
    translated = await translator.translate(view, "de", protected=["Gold Cover"])
    item = translated["domains"][1]["items"][0]
    assert item["name"] == "DE:Baggage Delay"
    assert item["amount"] == "EUR 300"
    assert translated["policy"] == "Gold Cover"
    assert len(translated["domains"]) == len(view["domains"])


async def test_translator_retries_drift_then_gives_up() -> None:
    translator, chat = _translator(lambda _: {"translations": {"unexpected": "x"}})
    view = customer_view(sample_record(), policy_label="Gold Cover")
    with pytest.raises(TranslationDriftError):
        await translator.translate(view, "fr", protected=[])
    assert len(chat.calls) >= 3
