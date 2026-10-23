"""Deterministic assembly of one versioned coverage record."""

from __future__ import annotations

from collections import defaultdict

from evidentia.modules.extraction.merge import MergedItem, rank_candidate, union_evidence
from evidentia.modules.extraction.normalize import amount_key, parse_amount, parse_currency
from evidentia.modules.extraction.schemas import (
    COVERAGE_FIELDS,
    Classification,
    CoverageEntry,
    CoverageItemDraft,
    CoverageRecord,
    DomainEntry,
    EvidencedValue,
    OrganizerOutput,
    PlanDomain,
    PlanSubdomain,
)
from evidentia.modules.extraction.taxonomy import (
    CATEGORIES,
    CATEGORY_ORDER,
    DOMAIN_ORDER,
    DOMAINS,
    ITEM_TYPES,
    UNCLASSIFIED,
    canonical_name,
    domain_for,
)


def _limit_value(draft: CoverageItemDraft) -> object:
    return draft.amount.value if not draft.amount.is_empty else draft.limit.value


def apply_duplicate_links(
    items: list[MergedItem], classifications: dict[str, Classification]
) -> tuple[list[MergedItem], int]:
    """Honours classifier duplicate links only when category and limit both match."""
    by_id = {item.id: item for item in items}
    removed: set[str] = set()
    for item in items:
        target_id = classifications[item.id].duplicate_of
        if not target_id or target_id in removed or item.id in removed:
            continue
        target = by_id[target_id]
        if classifications[target_id].category != classifications[item.id].category:
            continue
        if amount_key(_limit_value(item.draft)) != amount_key(_limit_value(target.draft)):
            continue
        primary, secondary = (
            (target, item)
            if rank_candidate(target.draft) >= rank_candidate(item.draft)
            else (item, target)
        )
        primary.draft = primary.draft.model_copy(
            update={"evidence": union_evidence(primary.draft.evidence, secondary.draft.evidence)}
        )
        primary.sources = sorted(set(primary.sources) | set(secondary.sources))
        removed.add(secondary.id)
    return [item for item in items if item.id not in removed], len(removed)


def _subdomain_label(category: str) -> str:
    return CATEGORIES[category].label if category in CATEGORIES else "Needs classification"


def deterministic_plan(items: list[MergedItem], categories: dict[str, str]) -> OrganizerOutput:
    """Rule-based organisation used when the organizer agent fails."""
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        category = categories[item.id]
        grouped[domain_for(category)][category].append(item.id)
    domains = []
    for domain_key in sorted(grouped, key=lambda key: DOMAIN_ORDER.get(key, 99)):
        subdomains = [
            PlanSubdomain(key=category, name=_subdomain_label(category), item_ids=sorted(ids))
            for category, ids in sorted(
                grouped[domain_key].items(), key=lambda pair: CATEGORY_ORDER.get(pair[0], 99)
            )
        ]
        domains.append(PlanDomain(key=domain_key, name=DOMAINS[domain_key], subdomains=subdomains))
    return OrganizerOutput(domains=domains)


def place_missing(
    plan: OrganizerOutput, missing: list[str], categories: dict[str, str]
) -> OrganizerOutput:
    """Adds items the organizer skipped to their category's default domain."""
    for item_id in missing:
        category = categories[item_id]
        domain_key = domain_for(category)
        domain = next((d for d in plan.domains if d.key == domain_key), None)
        if domain is None:
            domain = PlanDomain(key=domain_key, name=DOMAINS[domain_key], subdomains=[])
            plan.domains.append(domain)
        subdomain = next((s for s in domain.subdomains if s.key == category), None)
        if subdomain is None:
            subdomain = PlanSubdomain(key=category, name=_subdomain_label(category), item_ids=[])
            domain.subdomains.append(subdomain)
        subdomain.item_ids.append(item_id)
    return plan


def merge_general(sections: list[dict[str, EvidencedValue]]) -> dict[str, EvidencedValue]:
    best: dict[str, EvidencedValue] = {}
    for section in sections:
        for name, value in section.items():
            if value.is_empty:
                continue
            current = best.get(name)
            if current is None or value.confidence > current.confidence:
                best[name] = value
            elif value.value == current.value:
                best[name] = current.model_copy(
                    update={"evidence": union_evidence(current.evidence, value.evidence)}
                )
    return dict(sorted(best.items()))


def build_entry(
    item_id: str,
    draft: CoverageItemDraft,
    classification: Classification,
    *,
    domain: str,
    subdomain: str | None,
) -> CoverageEntry:
    title = " ".join(str(draft.title.value or "").split())
    amount_source = _limit_value(draft)
    item_type = str(draft.item_type.value or "").strip().lower().replace("-", "_")
    category = classification.category
    return CoverageEntry(
        id=item_id,
        category=category,
        subcategory=classification.subcategory,
        domain=domain,
        subdomain=subdomain,
        canonical_name=canonical_name(title),
        title=title,
        item_type=item_type if item_type in ITEM_TYPES else None,
        amount=parse_amount(amount_source),
        currency=parse_currency(draft.currency.value) or parse_currency(amount_source),
        customer_facing=CATEGORIES[category].customer_facing if category in CATEGORIES else False,
        confidence=draft.confidence,
        evidence=draft.evidence,
        fields={
            name: draft.field(name) for name in COVERAGE_FIELDS if not draft.field(name).is_empty
        },
    )


def sort_entries(entries: list[CoverageEntry]) -> list[CoverageEntry]:
    return sorted(
        entries,
        key=lambda e: (
            DOMAIN_ORDER.get(e.domain, 99),
            CATEGORY_ORDER.get(e.category, 99),
            e.canonical_name.lower(),
            e.id,
        ),
    )


def assemble_record(
    general: dict[str, EvidencedValue],
    items: list[MergedItem],
    classifications: dict[str, Classification],
    plan: OrganizerOutput,
) -> CoverageRecord:
    placement = {
        item_id: (domain.key, subdomain.key)
        for domain in plan.domains
        for subdomain in domain.subdomains
        for item_id in subdomain.item_ids
    }
    entries = []
    for item in items:
        classification = classifications.get(item.id) or Classification(
            item_id=item.id, category=UNCLASSIFIED
        )
        domain, subdomain = placement.get(
            item.id, (domain_for(classification.category), classification.category)
        )
        entries.append(
            build_entry(item.id, item.draft, classification, domain=domain, subdomain=subdomain)
        )
    entries = sort_entries(entries)
    order = {entry.id: index for index, entry in enumerate(entries)}
    domains = [
        DomainEntry(
            key=domain.key,
            name=domain.name,
            summary=domain.summary,
            comparison_signals=domain.comparison_signals,
            subdomains=[
                PlanSubdomain(
                    key=sub.key,
                    name=sub.name,
                    item_ids=sorted(sub.item_ids, key=lambda i: order.get(i, 1 << 30)),
                )
                for sub in domain.subdomains
                if sub.item_ids
            ],
        )
        for domain in sorted(plan.domains, key=lambda d: (DOMAIN_ORDER.get(d.key, 99), d.name))
    ]
    return CoverageRecord(general=general, domains=domains, coverages=entries)
