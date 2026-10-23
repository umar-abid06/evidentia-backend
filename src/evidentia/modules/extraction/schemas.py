"""JSON contracts between agents, and the assembled coverage record."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

COVERAGE_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "item_type",
    "amount",
    "limit",
    "sub_limit",
    "currency",
    "deductible",
    "basis",
    "waiting_period",
    "max_duration",
    "age_rules",
    "eligibility",
    "conditions",
    "exclusions",
    "geographic_scope",
    "claim_requirements",
    "table_rows",
)

GENERAL_FIELDS: tuple[str, ...] = (
    "insurer_name",
    "policy_name",
    "policy_number",
    "effective_date",
    "expiry_date",
    "max_trip_duration",
    "eligible_persons",
    "territorial_scope",
    "emergency_assistance",
    "governing_law",
)

RECORD_SCHEMA_VERSION: Literal["coverage-record/1"] = "coverage-record/1"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EvidenceRef(_Lenient):
    page: int = Field(ge=1)
    quote: str = Field(default="", max_length=1000)


class EvidencedValue(_Lenient):
    """Every extracted value carries its confidence and page-and-quote evidence."""

    value: JsonValue = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    notes: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.value is None or self.value in ("", [])


def _empty() -> EvidencedValue:
    return EvidencedValue()


class CoverageItemDraft(_Lenient):
    """Discovery output: one benefit, limit, exclusion or condition."""

    title: EvidencedValue
    description: EvidencedValue = Field(default_factory=_empty)
    item_type: EvidencedValue = Field(default_factory=_empty)
    amount: EvidencedValue = Field(default_factory=_empty)
    limit: EvidencedValue = Field(default_factory=_empty)
    sub_limit: EvidencedValue = Field(default_factory=_empty)
    currency: EvidencedValue = Field(default_factory=_empty)
    deductible: EvidencedValue = Field(default_factory=_empty)
    basis: EvidencedValue = Field(default_factory=_empty)
    waiting_period: EvidencedValue = Field(default_factory=_empty)
    max_duration: EvidencedValue = Field(default_factory=_empty)
    age_rules: EvidencedValue = Field(default_factory=_empty)
    eligibility: EvidencedValue = Field(default_factory=_empty)
    conditions: EvidencedValue = Field(default_factory=_empty)
    exclusions: EvidencedValue = Field(default_factory=_empty)
    geographic_scope: EvidencedValue = Field(default_factory=_empty)
    claim_requirements: EvidencedValue = Field(default_factory=_empty)
    table_rows: EvidencedValue = Field(default_factory=_empty)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    def field(self, name: str) -> EvidencedValue:
        value: EvidencedValue = getattr(self, name)
        return value

    def filled_fields(self) -> int:
        return sum(not self.field(name).is_empty for name in COVERAGE_FIELDS)

    def pages(self) -> set[int]:
        refs = list(self.evidence)
        for name in COVERAGE_FIELDS:
            refs.extend(self.field(name).evidence)
        return {ref.page for ref in refs}


class DiscoveryOutput(_Lenient):
    general: dict[str, EvidencedValue] = Field(default_factory=dict)
    items: list[CoverageItemDraft] = Field(default_factory=list)


class Classification(_Lenient):
    item_id: str
    category: str
    subcategory: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str | None = None
    duplicate_of: str | None = None


class ClassificationOutput(_Lenient):
    classifications: list[Classification]


class PlanSubdomain(_Lenient):
    key: str
    name: str
    item_ids: list[str]


class PlanDomain(_Lenient):
    key: str
    name: str
    summary: str = ""
    comparison_signals: list[str] = Field(default_factory=list)
    subdomains: list[PlanSubdomain]


class OrganizerOutput(_Lenient):
    domains: list[PlanDomain]


class Conflict(BaseModel):
    item_id: str
    field: str
    values: list[dict[str, Any]]


class CoverageEntry(BaseModel):
    id: str
    category: str
    subcategory: str | None = None
    domain: str
    subdomain: str | None = None
    canonical_name: str
    title: str
    item_type: str | None = None
    amount: float | None = None
    currency: str | None = None
    customer_facing: bool = True
    confidence: float
    evidence: list[EvidenceRef]
    fields: dict[str, EvidencedValue]


class DomainEntry(BaseModel):
    key: str
    name: str
    summary: str = ""
    comparison_signals: list[str] = Field(default_factory=list)
    subdomains: list[PlanSubdomain]


class CoverageRecord(BaseModel):
    """The versioned unit that is reviewed, published and embedded."""

    schema_version: Literal["coverage-record/1"] = RECORD_SCHEMA_VERSION
    general: dict[str, EvidencedValue] = Field(default_factory=dict)
    domains: list[DomainEntry] = Field(default_factory=list)
    coverages: list[CoverageEntry] = Field(default_factory=list)

    def coverage(self, item_id: str) -> CoverageEntry | None:
        return next((entry for entry in self.coverages if entry.id == item_id), None)
