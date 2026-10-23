"""Knowledge chunks: one complete fact per chunk, cut on the record's own structure.

Fixed-size windows would separate a benefit from its limit or exclusion; one chunk
per coverage item means a search hit always carries the whole answer. Output is
deterministic (stable order, stable keys), so republishing gives identical chunks.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from evidentia.ai.tokens import EMBEDDING_ENCODING, count_tokens
from evidentia.modules.extraction.schemas import CoverageEntry, CoverageRecord, DomainEntry
from evidentia.modules.extraction.taxonomy import CATEGORIES, DOMAINS
from evidentia.modules.knowledge.models import ChunkType

_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("limit", "Limit"),
    ("sub_limit", "Sub-limit"),
    ("deductible", "Deductible"),
    ("basis", "Basis"),
    ("waiting_period", "Waiting period"),
    ("max_duration", "Maximum duration"),
    ("age_rules", "Age rules"),
    ("eligibility", "Eligibility"),
    ("conditions", "Conditions"),
    ("exclusions", "Exclusions"),
    ("geographic_scope", "Geographic scope"),
    ("claim_requirements", "Claim requirements"),
)

_GENERAL_LABELS: dict[str, str] = {
    "insurer_name": "Insurer",
    "policy_name": "Policy",
    "policy_number": "Policy number",
    "effective_date": "Effective from",
    "expiry_date": "Valid until",
    "max_trip_duration": "Maximum trip duration",
    "eligible_persons": "Who is covered",
    "territorial_scope": "Where it applies",
    "emergency_assistance": "Emergency assistance",
    "governing_law": "Governing law",
}


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chunk_key: str
    ordinal: int
    chunk_type: ChunkType
    category: str
    domain: str
    title: str
    content: str
    customer_facing: bool
    token_count: int
    content_hash: str
    fields: dict[str, Any] = field(default_factory=dict)


def record_hash(record: CoverageRecord) -> str:
    canonical = json.dumps(
        record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(_text(item) for item in value if item not in (None, ""))
    return " ".join(str(value).split()) if value is not None else ""


def format_amount(amount: float | None, currency: str | None) -> str | None:
    if amount is None:
        return None
    number = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"
    return f"{currency} {number}" if currency else number


def _key(policy_id: uuid.UUID, kind: str, local_id: str) -> str:
    return hashlib.sha256(f"{policy_id}:{kind}:{local_id}".encode()).hexdigest()[:40]


def _pages(entry: CoverageEntry) -> list[int]:
    pages = {ref.page for ref in entry.evidence}
    for value in entry.fields.values():
        pages.update(ref.page for ref in value.evidence)
    return sorted(pages)


def _coverage_content(entry: CoverageEntry, policy_label: str) -> tuple[str, dict[str, Any]]:
    category = CATEGORIES.get(entry.category)
    lines = [f"{entry.canonical_name} ({category.label if category else entry.category})"]
    lines.append(f"Policy: {policy_label}")
    if entry.item_type:
        lines.append(f"Type: {entry.item_type.replace('_', ' ')}")
    amount = format_amount(entry.amount, entry.currency)
    raw_amount = entry.fields.get("amount")
    if amount or raw_amount:
        lines.append(f"Amount: {amount or _text(raw_amount.value if raw_amount else None)}")
    structured: dict[str, Any] = {
        "item_id": entry.id,
        "amount": entry.amount,
        "currency": entry.currency,
        "item_type": entry.item_type,
        "pages": _pages(entry),
    }
    for name, label in _FIELD_LABELS:
        value = entry.fields.get(name)
        if value is not None and not value.is_empty:
            text = _text(value.value)
            lines.append(f"{label}: {text}")
            structured[name] = text
    description = entry.fields.get("description")
    if description is not None and not description.is_empty:
        lines.append(f"Details: {_text(description.value)}")
    return "\n".join(lines), structured


def _domain_content(domain: DomainEntry, record: CoverageRecord, policy_label: str) -> str:
    names = [
        entry.canonical_name
        for sub in domain.subdomains
        for item_id in sub.item_ids
        if (entry := record.coverage(item_id)) is not None
    ]
    lines = [f"{domain.name} overview", f"Policy: {policy_label}"]
    if domain.summary:
        lines.append(domain.summary)
    if names:
        lines.append("Includes: " + ", ".join(names))
    if domain.comparison_signals:
        lines.append("Compare on: " + ", ".join(domain.comparison_signals))
    return "\n".join(lines)


def build_knowledge_chunks(
    policy_id: uuid.UUID, record: CoverageRecord, *, policy_label: str
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []

    def add(
        key: str,
        chunk_type: ChunkType,
        *,
        category: str,
        domain: str,
        title: str,
        content: str,
        customer_facing: bool,
        fields: dict[str, Any],
    ) -> None:
        drafts.append(
            ChunkDraft(
                chunk_key=key,
                ordinal=len(drafts),
                chunk_type=chunk_type,
                category=category,
                domain=domain,
                title=title[:300],
                content=content,
                customer_facing=customer_facing,
                token_count=count_tokens(content, encoding=EMBEDDING_ENCODING),
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                fields=fields,
            )
        )

    general_lines = [
        f"{label}: {_text(record.general[name].value)}"
        for name, label in _GENERAL_LABELS.items()
        if name in record.general and not record.general[name].is_empty
    ]
    if general_lines:
        add(
            _key(policy_id, "overview", "general"),
            ChunkType.OVERVIEW,
            category="overview",
            domain="overview",
            title=f"{policy_label} at a glance",
            content="\n".join([f"{policy_label} at a glance", *general_lines]),
            customer_facing=True,
            fields={"section": "general"},
        )
    for domain in record.domains:
        add(
            _key(policy_id, "domain", domain.key),
            ChunkType.OVERVIEW,
            category="overview",
            domain=domain.key,
            title=f"{domain.name} overview",
            content=_domain_content(domain, record, policy_label),
            customer_facing=True,
            fields={"section": "domain", "domain_name": DOMAINS.get(domain.key, domain.name)},
        )
    for entry in record.coverages:
        content, structured = _coverage_content(entry, policy_label)
        add(
            _key(policy_id, "coverage", entry.id),
            ChunkType.COVERAGE,
            category=entry.category,
            domain=entry.domain,
            title=entry.canonical_name,
            content=content,
            customer_facing=entry.customer_facing,
            fields=structured,
        )
    return drafts
