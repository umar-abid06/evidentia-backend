from __future__ import annotations

from typing import Any

import pytest

from evidentia.ai.prompts import PromptRegistry
from evidentia.ai.types import ChatRequest
from evidentia.modules.documents.text import PageText
from evidentia.modules.extraction.amendments import diff_records, reconcile
from evidentia.modules.extraction.assembly import apply_duplicate_links, deterministic_plan
from evidentia.modules.extraction.chunking import build_reading_chunks
from evidentia.modules.extraction.merge import MergedItem, merge_items
from evidentia.modules.extraction.normalize import parse_amount, parse_currency
from evidentia.modules.extraction.pipeline import ExtractionPipeline
from evidentia.modules.extraction.review import review_gate
from evidentia.modules.extraction.schemas import (
    Classification,
    CoverageEntry,
    CoverageItemDraft,
    CoverageRecord,
    EvidencedValue,
    EvidenceRef,
)
from tests.fakes import PolicyReader, ev, item


def _pages(*texts: str) -> list[PageText]:
    return [PageText(number=i, text=text) for i, text in enumerate(texts, start=1)]


def _draft(title: str, page: int, **kwargs: Any) -> CoverageItemDraft:
    return CoverageItemDraft.model_validate(item(title, page, **kwargs))


def test_reading_chunks_pack_whole_pages_with_markers() -> None:
    chunks = build_reading_chunks(_pages("a" * 40, "b" * 40, "", "c" * 40), max_chars=110)
    assert [chunk.pages for chunk in chunks] == [(1, 2), (4,)]
    assert chunks[0].text.startswith("[PAGE 1]\n")
    assert "[PAGE 2]" in chunks[0].text


def test_an_oversized_page_is_never_split() -> None:
    chunks = build_reading_chunks(_pages("x" * 500, "y" * 10), max_chars=100)
    assert [chunk.pages for chunk in chunks] == [(1,), (2,)]
    assert "x" * 500 in chunks[0].text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EUR 1,000,000", 1_000_000.0),
        ("1.000.000 €", 1_000_000.0),
        ("up to 2 500 EUR per person", 2_500.0),
        ("USD 5m", 5_000_000.0),
        ("€250.50", 250.5),
        ("1.250,75 EUR", 1250.75),
        (300, 300.0),
        ("unlimited", None),
    ],
)
def test_amounts_are_parsed(raw: object, expected: float | None) -> None:
    assert parse_amount(raw) == expected


def test_currencies_are_normalised() -> None:
    assert parse_currency("€ 300") == "EUR"
    assert parse_currency("up to GBP 500") == "GBP"
    assert parse_currency("CHF 1'000") == "CHF"
    assert parse_currency("unlimited") is None


def test_merge_collapses_duplicates_and_records_conflicts() -> None:
    first = _draft("Emergency medical expenses", 3, amount="EUR 1,000,000", deductible="EUR 50")
    second = _draft(
        "Emergency Medical Expenses",
        3,
        amount="EUR 1,000,000",
        deductible="EUR 100",
        confidence=0.99,
    )
    result = merge_items([(0, first), (1, second)])
    assert len(result.items) == 1
    merged = result.items[0]
    assert merged.sources == [0, 1]
    assert merged.draft.deductible.value == "EUR 100"  # higher confidence wins
    assert [conflict.field for conflict in result.conflicts] == ["deductible"]


def test_items_without_evidence_are_dropped() -> None:
    draft = CoverageItemDraft(title=EvidencedValue(value="Mystery benefit"))
    result = merge_items([(0, draft)])
    assert result.items == []
    assert result.dropped == 1


def test_duplicate_links_need_same_category_and_limit() -> None:
    a = MergedItem("cov_a", _draft("Baggage delay", 4, amount="EUR 300"))
    b = MergedItem("cov_b", _draft("Delayed baggage", 9, amount="EUR 300"))
    c = MergedItem("cov_c", _draft("Baggage delay extra", 9, amount="EUR 500"))
    classes = {
        "cov_a": Classification(item_id="cov_a", category="baggage_delay"),
        "cov_b": Classification(item_id="cov_b", category="baggage_delay", duplicate_of="cov_a"),
        "cov_c": Classification(item_id="cov_c", category="baggage_delay", duplicate_of="cov_a"),
    }
    kept, linked = apply_duplicate_links([a, b, c], classes)
    assert linked == 1
    assert [i.id for i in kept] == ["cov_a", "cov_c"]
    assert {ref.page for ref in kept[0].draft.evidence} == {4, 9}


def test_deterministic_plan_places_every_item_exactly_once() -> None:
    items = [MergedItem(f"cov_{n}", _draft(f"Item {n}", 1)) for n in range(4)]
    categories = {
        "cov_0": "emergency_medical",
        "cov_1": "dental",
        "cov_2": "baggage_delay",
        "cov_3": "unclassified",
    }
    plan = deterministic_plan(items, categories)
    placed = [i for d in plan.domains for s in d.subdomains for i in s.item_ids]
    assert sorted(placed) == sorted(categories)
    assert [d.key for d in plan.domains] == ["medical", "baggage", "terms"]


def test_review_gate_reports_each_reason() -> None:
    record = CoverageRecord(
        coverages=[
            CoverageEntry(
                id="cov_1",
                category="dental",
                domain="medical",
                canonical_name="Dental",
                title="Dental",
                confidence=0.5,
                evidence=[EvidenceRef(page=1, quote="Dental")],
                fields={},
            )
        ]
    )
    confidence, reasons = review_gate(
        record, conflicts=[], unplaced=["cov_9"], dropped=2, threshold=0.8, sparse=False
    )
    assert confidence == 0.5
    assert {r.code for r in reasons} == {
        "low_confidence",
        "unplaced_items",
        "unsupported_items",
        "missing_general_data",
    }


POLICY_PAGES = {
    1: [item("Emergency medical expenses", 1, amount="EUR 1,000,000", currency="EUR")],
    2: [
        item("Emergency dental treatment", 2, amount="EUR 500"),
        item("Baggage delay", 2, amount="EUR 300", conditions="after 6 hours"),
    ],
    3: [
        item("Baggage delay", 3, amount="EUR 300"),
        item("Rental car excess", 3, amount="EUR 2,500"),
    ],
    4: [item("War and civil unrest", 4, item_type="exclusion")],
}


async def test_pipeline_produces_a_clean_record_offline() -> None:
    gateway, chat = PolicyReader(POLICY_PAGES).gateway()
    pipeline = ExtractionPipeline(gateway, PromptRegistry(environ={}), chunk_chars=120)
    pages = _pages("medical " * 5, "dental and baggage", "baggage and rental", "war")
    outcome = await pipeline.run(pages, mode="full")

    record = outcome.record
    titles = [entry.canonical_name for entry in record.coverages]
    assert "Emergency Medical Expenses" in titles
    assert "Rental Vehicle Excess" in titles
    assert len(record.coverages) == 6  # baggage delay on p2 and p3 have different pages
    assert outcome.review_reasons == []
    assert outcome.overall_confidence == pytest.approx(0.95)
    medical = next(e for e in record.coverages if e.category == "emergency_medical")
    assert (medical.amount, medical.currency, medical.domain) == (1_000_000.0, "EUR", "medical")

    stages = [call.stage for call in chat.calls]
    assert stages.count("discovery") == outcome.stats["reading_chunks"] > 1
    assert stages.count("classify") == 1
    assert stages.count("organize") == 1
    assert record.domains[0].key == "medical"


async def test_organizer_plan_that_repeats_items_is_replaced() -> None:
    reader = PolicyReader(POLICY_PAGES)

    def greedy_organizer(request: ChatRequest) -> dict[str, Any]:
        plan = reader.organize(request)
        first = plan["domains"][0]["subdomains"][0]
        first["item_ids"] = first["item_ids"] * 2
        return plan

    gateway, chat = reader.gateway()
    chat.on("organize", greedy_organizer)
    outcome = await ExtractionPipeline(gateway, PromptRegistry(environ={})).run(
        _pages("a", "b", "c", "d"), mode="full"
    )
    assert outcome.stats["organizer_fallback"] is True
    placed = [i for d in outcome.record.domains for s in d.subdomains for i in s.item_ids]
    assert sorted(placed) == sorted(e.id for e in outcome.record.coverages)


async def test_amendments_run_in_sparse_mode_without_the_organizer() -> None:
    gateway, chat = PolicyReader(
        {1: [item("Baggage delay", 1, amount="EUR 400")]}, general={}
    ).gateway()
    outcome = await ExtractionPipeline(gateway, PromptRegistry(environ={})).run(
        _pages("endorsement"), mode="amendment"
    )
    discovery = next(call for call in chat.calls if call.stage == "discovery")
    assert "Omission means no change" in discovery.messages[0].content
    assert "organize" not in [call.stage for call in chat.calls]
    assert outcome.review_reasons == []


def _entry(item_id: str, title: str, category: str, numeric: float, **fields: Any) -> CoverageEntry:
    return CoverageEntry(
        id=item_id,
        category=category,
        domain="baggage",
        canonical_name=title,
        title=title,
        amount=numeric,
        currency="EUR",
        confidence=0.9,
        evidence=[EvidenceRef(page=1, quote=title)],
        fields={name: EvidencedValue.model_validate(ev(v, 1)) for name, v in fields.items()},
    )


def test_reconcile_patches_adds_and_never_deletes() -> None:
    base = CoverageRecord(
        coverages=[
            _entry("cov_a", "Baggage delay", "baggage_delay", 300, amount="EUR 300", basis="trip"),
            _entry("cov_b", "Baggage loss", "baggage_loss", 1000, amount="EUR 1,000"),
        ]
    )
    patch = CoverageRecord(
        coverages=[
            _entry("cov_x", "Baggage delay", "baggage_delay", 400, amount="EUR 400"),
            _entry("cov_y", "Gadget cover", "purchase_protection", 800, amount="EUR 800"),
        ]
    )
    result = reconcile(base, patch)
    delay = result.record.coverage("cov_a")
    assert delay is not None
    assert delay.amount == 400
    assert delay.fields["basis"].value == "trip"  # untouched field keeps base value
    assert result.record.coverage("cov_b") is not None  # omission never deletes
    assert result.record.coverage("cov_y") is not None
    paths = {change.path: change.action for change in result.changes}
    assert paths["coverages.cov_a.fields.amount"] == "updated"
    assert paths["coverages.cov_a.amount"] == "updated"
    assert paths["coverages.cov_y"] == "added"


def test_reconcile_blocks_short_generic_titles() -> None:
    base = CoverageRecord(
        coverages=[_entry("cov_a", "Medical expenses abroad", "emergency_medical", 1)]
    )
    patch = CoverageRecord(coverages=[_entry("cov_x", "Medical", "emergency_medical", 2)])
    result = reconcile(base, patch)
    assert result.record.coverage("cov_a") is not None
    assert result.record.coverage("cov_a").amount == 1  # type: ignore[union-attr]
    assert [c.action for c in result.changes] == ["added"]


def test_diff_records_reports_field_level_changes() -> None:
    old = CoverageRecord(
        coverages=[_entry("cov_a", "Baggage delay", "baggage_delay", 300, amount="EUR 300")]
    )
    new = CoverageRecord(
        coverages=[_entry("cov_a", "Baggage delay", "baggage_delay", 300, amount="EUR 350")]
    )
    [change] = diff_records(old, new)
    assert (change.path, change.old, change.new) == (
        "coverages.cov_a.fields.amount",
        "EUR 300",
        "EUR 350",
    )
