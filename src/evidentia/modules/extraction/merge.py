"""Stitches chunk results back together.

Neighbouring chunks often see the same benefit twice. Items are fingerprinted from
title, amount and page; duplicates collapse into one. When candidates disagree,
the highest confidence wins, then the most evidence, then the richest detail.
Every quote and page is kept and each disagreement is recorded for the reviewer.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field

from evidentia.modules.extraction.normalize import amount_key
from evidentia.modules.extraction.schemas import (
    COVERAGE_FIELDS,
    Conflict,
    CoverageItemDraft,
    EvidencedValue,
    EvidenceRef,
)
from evidentia.modules.extraction.taxonomy import normalize_title


@dataclass(slots=True)
class MergedItem:
    id: str
    draft: CoverageItemDraft
    sources: list[int] = field(default_factory=list)


@dataclass(slots=True)
class MergeResult:
    items: list[MergedItem]
    conflicts: list[Conflict]
    dropped: int


def fingerprint(item: CoverageItemDraft) -> str:
    title = normalize_title(str(item.title.value or ""))
    amount_source = item.amount.value if not item.amount.is_empty else item.limit.value
    pages = item.pages()
    first_page = min(pages) if pages else 0
    raw = f"{title}|{amount_key(amount_source)}|{first_page}"
    return hashlib.sha256(raw.encode()).hexdigest()


def rank_candidate(item: CoverageItemDraft) -> tuple[float, int, int]:
    return (item.confidence, len(item.evidence), item.filled_fields())


def union_evidence(*groups: list[EvidenceRef]) -> list[EvidenceRef]:
    seen: dict[tuple[int, str], EvidenceRef] = {}
    for refs in groups:
        for ref in refs:
            seen.setdefault((ref.page, ref.quote.strip()), ref)
    return sorted(seen.values(), key=lambda ref: (ref.page, ref.quote))


def _comparable(value: EvidencedValue) -> str:
    return json.dumps(value.value, sort_keys=True, default=str).lower()


def merge_items(found: list[tuple[int, CoverageItemDraft]]) -> MergeResult:
    """``found`` holds (chunk index, item) pairs in reading order."""
    groups: dict[str, list[tuple[int, CoverageItemDraft]]] = defaultdict(list)
    dropped = 0
    for chunk_index, item in found:
        if item.title.is_empty or not item.pages():
            dropped += 1  # no title or no evidence: nothing a reviewer could verify
            continue
        groups[fingerprint(item)].append((chunk_index, item))

    merged: list[MergedItem] = []
    conflicts: list[Conflict] = []
    for key, members in groups.items():
        item_id = f"cov_{key[:12]}"
        best = max((item for _, item in members), key=rank_candidate)
        fields: dict[str, EvidencedValue] = {}
        for name in COVERAGE_FIELDS:
            chosen = best.field(name)
            candidates = [item.field(name) for _, item in members if not item.field(name).is_empty]
            distinct = {_comparable(value) for value in candidates}
            if len(distinct) > 1:
                conflicts.append(
                    Conflict(
                        item_id=item_id,
                        field=name,
                        values=[
                            {
                                "value": value.value,
                                "confidence": value.confidence,
                                "pages": sorted({ref.page for ref in value.evidence}),
                            }
                            for value in candidates
                        ],
                    )
                )
            same = [value for value in candidates if _comparable(value) == _comparable(chosen)]
            fields[name] = chosen.model_copy(
                update={
                    "evidence": union_evidence(chosen.evidence, *(value.evidence for value in same))
                }
            )
        draft = best.model_copy(
            update={**fields, "evidence": union_evidence(*(item.evidence for _, item in members))}
        )
        merged.append(
            MergedItem(id=item_id, draft=draft, sources=sorted({index for index, _ in members}))
        )

    merged.sort(key=lambda item: (min(item.draft.pages()), item.id))
    return MergeResult(items=merged, conflicts=conflicts, dropped=dropped)
