"""Stable coverage taxonomy: 19 categories grouped into customer-facing domains."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Category:
    key: str
    label: str
    domain: str
    customer_facing: bool = True


DOMAINS: dict[str, str] = {
    "medical": "Medical",
    "travel_disruption": "Travel Disruption",
    "baggage": "Baggage & Belongings",
    "liability_legal": "Liability & Legal",
    "accident": "Personal Accident",
    "vehicle": "Vehicle",
    "purchase_protection": "Purchase Protection",
    "assistance": "Assistance",
    "terms": "Exclusions & Conditions",
}

_CATEGORIES = (
    Category("emergency_medical", "Emergency medical expenses", "medical"),
    Category("medical_evacuation", "Medical evacuation and repatriation", "medical"),
    Category("dental", "Emergency dental treatment", "medical"),
    Category("hospital_allowance", "Hospital cash allowance", "medical"),
    Category("trip_cancellation", "Trip cancellation", "travel_disruption"),
    Category("trip_interruption", "Trip interruption and curtailment", "travel_disruption"),
    Category("travel_delay", "Travel delay", "travel_disruption"),
    Category("missed_connection", "Missed departure and connection", "travel_disruption"),
    Category("baggage_loss", "Baggage loss, theft and damage", "baggage"),
    Category("baggage_delay", "Baggage delay", "baggage"),
    Category("personal_liability", "Personal liability", "liability_legal"),
    Category("legal_expenses", "Legal expenses", "liability_legal"),
    Category("personal_accident", "Personal accident", "accident"),
    Category("rental_vehicle", "Rental vehicle excess", "vehicle"),
    Category("purchase_protection", "Purchase protection", "purchase_protection"),
    Category("extended_warranty", "Extended warranty", "purchase_protection"),
    Category("assistance_services", "Assistance services", "assistance"),
    Category("general_exclusions", "General exclusions", "terms"),
    Category("general_conditions", "General conditions and definitions", "terms", False),
)

CATEGORIES: dict[str, Category] = {category.key: category for category in _CATEGORIES}
DOMAIN_ORDER: dict[str, int] = {key: index for index, key in enumerate(DOMAINS)}
CATEGORY_ORDER: dict[str, int] = {key: index for index, key in enumerate(CATEGORIES)}
UNCLASSIFIED = "unclassified"

ITEM_TYPES = ("benefit", "limit", "sub_limit", "exclusion", "condition", "service")

# Aliases (normalized) -> canonical benefit name. Extend as new wordings appear.
CANONICAL_BENEFITS: dict[str, str] = {
    "emergency medical expenses": "Emergency Medical Expenses",
    "medical expenses abroad": "Emergency Medical Expenses",
    "emergency medical treatment": "Emergency Medical Expenses",
    "medical treatment abroad": "Emergency Medical Expenses",
    "medical evacuation": "Medical Evacuation",
    "emergency medical evacuation": "Medical Evacuation",
    "repatriation of mortal remains": "Repatriation of Remains",
    "emergency dental treatment": "Emergency Dental Treatment",
    "dental emergency": "Emergency Dental Treatment",
    "trip cancellation": "Trip Cancellation",
    "cancellation costs": "Trip Cancellation",
    "trip curtailment": "Trip Interruption",
    "trip interruption": "Trip Interruption",
    "travel delay": "Travel Delay",
    "flight delay": "Travel Delay",
    "missed departure": "Missed Departure",
    "missed connection": "Missed Connection",
    "baggage delay": "Baggage Delay",
    "delayed baggage": "Baggage Delay",
    "lost baggage": "Baggage Loss",
    "baggage loss": "Baggage Loss",
    "personal liability": "Personal Liability",
    "legal expenses": "Legal Expenses",
    "personal accident": "Personal Accident",
    "accidental death": "Accidental Death",
    "rental car excess": "Rental Vehicle Excess",
    "rental vehicle excess": "Rental Vehicle Excess",
    "purchase protection": "Purchase Protection",
    "extended warranty": "Extended Warranty",
}

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    return _NON_WORD.sub(" ", title.lower()).strip()


def canonical_name(title: str) -> str:
    normalized = normalize_title(title)
    if normalized in CANONICAL_BENEFITS:
        return CANONICAL_BENEFITS[normalized]
    cleaned = " ".join(title.split()).strip(" .:;-")
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def domain_for(category_key: str) -> str:
    category = CATEGORIES.get(category_key)
    return category.domain if category else "terms"


def taxonomy_prompt_block() -> str:
    lines = [f"- {c.key}: {c.label} (domain: {DOMAINS[c.domain]})" for c in _CATEGORIES]
    return "\n".join(lines)
