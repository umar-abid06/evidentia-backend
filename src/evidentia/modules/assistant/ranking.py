"""In-memory selection over one wide candidate pool, weighted by intent."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from evidentia.modules.assistant.intent import IntentKind
from evidentia.modules.extraction.taxonomy import CATEGORY_ORDER


@dataclass(frozen=True, slots=True)
class Candidate:
    chunk_id: uuid.UUID
    policy_id: uuid.UUID
    version_id: uuid.UUID
    chunk_type: str
    category: str
    domain: str
    title: str
    content: str
    customer_facing: bool
    similarity: float
    ordinal: int = 0
    fields: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class SelectionLimits:
    floor: float = 0.45
    relaxed_floor: float = 0.40
    per_policy: int = 5
    total: int = 15


@dataclass(slots=True)
class Selection:
    chunks: list[Candidate]
    floor_applied: float | None
    strong_candidates: int
    considered: int


def select_semantic(
    candidates: list[Candidate],
    *,
    intent: IntentKind,
    topic_categories: set[str],
    topics_detected: bool,
    limits: SelectionLimits,
) -> Selection:
    """Strict grounding floor, relaxed only for known topics with thin evidence."""
    strong = [c for c in candidates if c.similarity >= limits.floor]
    floor = limits.floor
    if topics_detected and len(strong) < 2:
        floor = limits.relaxed_floor
    pool = sorted(
        (c for c in candidates if c.similarity >= floor),
        key=lambda c: (-c.similarity, c.ordinal),
    )

    selected: list[Candidate] = []
    per_policy: Counter[uuid.UUID] = Counter()
    categories: set[str] = set()
    policies: set[uuid.UUID] = set()

    def score(candidate: Candidate) -> float:
        if intent == "explain":
            return 0.75 * candidate.similarity + 0.25 * (candidate.category not in categories)
        if intent == "compare_specific":
            return (
                0.60 * candidate.similarity
                + 0.30 * (candidate.policy_id not in policies)
                + 0.10 * (candidate.category in topic_categories)
            )
        return candidate.similarity

    while pool and len(selected) < limits.total:
        eligible = [c for c in pool if per_policy[c.policy_id] < limits.per_policy]
        if not eligible:
            break
        best = max(eligible, key=score)
        selected.append(best)
        pool.remove(best)
        per_policy[best.policy_id] += 1
        categories.add(best.category)
        policies.add(best.policy_id)

    return Selection(
        chunks=selected,
        floor_applied=floor,
        strong_candidates=len(strong),
        considered=len(candidates),
    )


def select_representatives(
    chunks: list[Candidate], *, limits: SelectionLimits, customer_facing_only: bool
) -> Selection:
    """Summary route: a balanced set across categories, with zero embedding calls."""
    by_policy: dict[uuid.UUID, dict[str, list[Candidate]]] = defaultdict(lambda: defaultdict(list))
    for chunk in sorted(chunks, key=lambda c: c.ordinal):
        if chunk.chunk_type != "coverage" or (customer_facing_only and not chunk.customer_facing):
            continue
        by_policy[chunk.policy_id][chunk.category].append(chunk)

    picks: dict[uuid.UUID, list[Candidate]] = {}
    for policy_id, groups in by_policy.items():
        ordered = sorted(groups, key=lambda key: CATEGORY_ORDER.get(key, 99))
        chosen: list[Candidate] = []
        depth = 0
        while len(chosen) < limits.per_policy and any(len(groups[k]) > depth for k in ordered):
            for key in ordered:
                if len(groups[key]) > depth and len(chosen) < limits.per_policy:
                    chosen.append(groups[key][depth])
            depth += 1
        picks[policy_id] = chosen

    selected: list[Candidate] = []
    rank = 0
    while len(selected) < limits.total and any(len(p) > rank for p in picks.values()):
        for chosen in picks.values():
            if len(chosen) > rank and len(selected) < limits.total:
                selected.append(chosen[rank])
        rank += 1
    return Selection(
        chunks=selected, floor_applied=None, strong_candidates=0, considered=len(chunks)
    )
