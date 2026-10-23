"""Registry of customer concepts: how customers say it, and how policies say it.

Expansion terms only help find candidates; they never count as proof that a
coverage exists. Only retrieved policy facts can do that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_EXPANSION_TERMS = 12


@dataclass(frozen=True, slots=True)
class Concept:
    key: str
    label: str
    keywords: tuple[str, ...]
    insurer_terms: tuple[str, ...]
    categories: tuple[str, ...]


CONCEPTS: tuple[Concept, ...] = (
    Concept(
        "baggage_delay",
        "baggage delay",
        (
            "baggage delay",
            "luggage delay",
            "delayed luggage",
            "delayed baggage",
            "bag delayed",
            "luggage delayed",
            "luggage arrived late",
            "suitcase delayed",
        ),
        ("baggage delay", "delayed baggage", "essential purchases", "emergency replacement items"),
        ("baggage_delay",),
    ),
    Concept(
        "dental",
        "dental treatment",
        ("dentist", "dental", "tooth", "teeth", "toothache", "filling"),
        ("emergency dental treatment", "dental pain relief", "dental emergency"),
        ("dental",),
    ),
    Concept(
        "evacuation",
        "medical evacuation",
        (
            "evacuation",
            "repatriation",
            "air ambulance",
            "airlift",
            "fly me home",
            "transport home",
            "medical flight",
        ),
        (
            "medical evacuation",
            "medical repatriation",
            "repatriation of mortal remains",
            "emergency medical transport",
        ),
        ("medical_evacuation",),
    ),
    Concept(
        "medical",
        "medical treatment",
        (
            "doctor",
            "hospital",
            "sick",
            "ill",
            "illness",
            "medical",
            "treatment",
            "clinic",
            "injury",
            "injured",
            "medicine",
            "prescription",
            "emergency room",
            "covid",
        ),
        (
            "emergency medical expenses",
            "physician fees",
            "hospitalisation",
            "in-patient treatment",
            "out-patient treatment",
            "prescribed medication",
            "medically necessary treatment",
        ),
        ("emergency_medical", "hospital_allowance"),
    ),
    Concept(
        "trip_cancellation",
        "trip cancellation",
        ("cancel", "cancellation", "cancelled trip", "can't travel", "cannot travel", "call off"),
        (
            "trip cancellation",
            "cancellation costs",
            "non-refundable travel costs",
            "cancellation charges",
        ),
        ("trip_cancellation",),
    ),
    Concept(
        "trip_interruption",
        "trip interruption",
        ("cut short", "interrupt", "interruption", "come home early", "curtail", "early return"),
        ("trip interruption", "curtailment", "unused travel arrangements"),
        ("trip_interruption",),
    ),
    Concept(
        "missed_connection",
        "missed connection",
        (
            "missed connection",
            "missed flight",
            "missed departure",
            "connecting flight",
            "missed my flight",
        ),
        ("missed departure", "missed connection", "alternative transport costs"),
        ("missed_connection",),
    ),
    Concept(
        "travel_delay",
        "travel delay",
        ("delay", "delayed", "late flight", "flight late", "overbooked"),
        ("travel delay", "flight delay", "departure delay", "delay benefit"),
        ("travel_delay",),
    ),
    Concept(
        "baggage",
        "baggage loss",
        ("luggage", "baggage", "suitcase", "bag", "belongings", "stolen", "lost", "theft"),
        ("baggage loss", "personal belongings", "theft of baggage", "damaged baggage"),
        ("baggage_loss",),
    ),
    Concept(
        "liability",
        "personal liability",
        (
            "liability",
            "third party",
            "sued",
            "damage to someone",
            "injure someone",
            "legal costs",
            "lawyer",
        ),
        ("personal liability", "third-party liability", "legal liability", "legal expenses"),
        ("personal_liability", "legal_expenses"),
    ),
    Concept(
        "accident",
        "personal accident",
        ("accident", "accidental", "death", "disability", "disabled"),
        ("personal accident", "accidental death", "permanent disability"),
        ("personal_accident",),
    ),
    Concept(
        "rental_car",
        "rental car",
        ("rental car", "car hire", "hire car", "rented car", "rental vehicle", "car rental"),
        ("rental vehicle excess", "car hire excess", "collision damage waiver"),
        ("rental_vehicle",),
    ),
    Concept(
        "purchase_protection",
        "purchase protection",
        ("purchase", "purchased", "bought", "warranty", "item broke", "broken item"),
        ("purchase protection", "extended warranty", "price protection"),
        ("purchase_protection", "extended_warranty"),
    ),
    Concept(
        "claims",
        "claims",
        ("claim", "claims", "reimburse", "reimbursement", "receipts", "paperwork"),
        ("claim requirements", "claims procedure", "supporting documents", "notification period"),
        (),
    ),
)

CONCEPTS_BY_KEY: dict[str, Concept] = {concept.key: concept for concept in CONCEPTS}


def _pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])")


_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    c.key: [_pattern(k) for k in sorted(c.keywords, key=len, reverse=True)] for c in CONCEPTS
}


def detect_topics(normalized_question: str) -> list[str]:
    """Concepts mentioned in the question; specific concepts shadow broader ones."""
    found = [
        concept.key
        for concept in CONCEPTS
        if any(p.search(normalized_question) for p in _PATTERNS[concept.key])
    ]
    shadowed = {
        "baggage_delay": {"travel_delay", "baggage"},
        "missed_connection": {"travel_delay"},
        "dental": {"medical"},
        "evacuation": {"medical"},
    }
    hidden = {broad for key in found for broad in shadowed.get(key, set())}
    return [key for key in found if key not in hidden]


def expansion_terms(topics: list[str], *, limit: int = MAX_EXPANSION_TERMS) -> list[str]:
    terms: list[str] = []
    for key in topics:
        for term in CONCEPTS_BY_KEY[key].insurer_terms:
            if term not in terms:
                terms.append(term)
    return terms[:limit]


def topic_categories(topics: list[str]) -> set[str]:
    return {category for key in topics for category in CONCEPTS_BY_KEY[key].categories}
