"""Amendment reconciliation: apply a sparse patch to the live record, surgically.

Changed items are matched to the base by category, then normalised title, then
containment (short generic titles are blocked). Matched items take the new values,
unmentioned fields keep the base values, new benefits are added, and nothing is
ever deleted by omission. Every change is logged with path, action, old and new.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from evidentia.modules.extraction.assembly import sort_entries
from evidentia.modules.extraction.schemas import (
    CoverageEntry,
    CoverageRecord,
    DomainEntry,
    EvidencedValue,
    PlanSubdomain,
)
from evidentia.modules.extraction.taxonomy import CATEGORIES, DOMAINS, normalize_title

GENERIC_TITLES = frozenset(
    {
        "benefit",
        "benefits",
        "conditions",
        "cover",
        "coverage",
        "definitions",
        "exclusions",
        "general",
        "insurance",
        "limit",
        "limits",
        "medical",
        "baggage",
        "travel",
    }
)


ENTRY_ATTRIBUTES = ("category", "canonical_name", "amount", "currency", "customer_facing")


class FieldChange(BaseModel):
    path: str
    action: Literal["added", "updated", "removed"]
    old: Any = None
    new: Any = None


class ReconcileResult(BaseModel):
    record: CoverageRecord
    changes: list[FieldChange]
    ambiguous: list[str]


def _is_generic(normalized: str) -> bool:
    return normalized in GENERIC_TITLES or len(normalized.split()) < 2


def _dump(value: EvidencedValue | None) -> str:
    return json.dumps(value.value if value else None, sort_keys=True, default=str).lower()


def _same(a: EvidencedValue | None, b: EvidencedValue | None) -> bool:
    return _dump(a) == _dump(b)


def match_base(
    entry: CoverageEntry, base: list[CoverageEntry]
) -> tuple[CoverageEntry | None, bool]:
    """Returns (match, ambiguous). Declines to guess when evidence is ambiguous."""
    candidates = [b for b in base if b.category == entry.category]
    title = normalize_title(entry.title)
    canonical = normalize_title(entry.canonical_name)
    exact = [
        b
        for b in candidates
        if normalize_title(b.title) == title or normalize_title(b.canonical_name) == canonical
    ]
    if len(exact) == 1:
        return exact[0], False
    if len(exact) > 1:
        return None, True
    if _is_generic(title):
        return None, False
    contained = [
        b
        for b in candidates
        if not _is_generic(other := normalize_title(b.title)) and (title in other or other in title)
    ]
    if len(contained) == 1:
        return contained[0], False
    return None, len(contained) > 1


def _place(record: CoverageRecord, entry: CoverageEntry) -> None:
    domain = next((d for d in record.domains if d.key == entry.domain), None)
    if domain is None:
        domain = DomainEntry(
            key=entry.domain, name=DOMAINS.get(entry.domain, entry.domain), subdomains=[]
        )
        record.domains.append(domain)
    sub_key = entry.subdomain or entry.category
    subdomain = next((s for s in domain.subdomains if s.key == sub_key), None)
    if subdomain is None:
        label = CATEGORIES[entry.category].label if entry.category in CATEGORIES else sub_key
        subdomain = PlanSubdomain(key=sub_key, name=label, item_ids=[])
        domain.subdomains.append(subdomain)
    subdomain.item_ids.append(entry.id)


def reconcile(base: CoverageRecord, patch: CoverageRecord) -> ReconcileResult:
    record = base.model_copy(deep=True)
    changes: list[FieldChange] = []
    ambiguous: list[str] = []

    for name, value in patch.general.items():
        current = record.general.get(name)
        if not value.is_empty and not _same(current, value):
            changes.append(
                FieldChange(
                    path=f"general.{name}",
                    action="updated" if current else "added",
                    old=current.value if current else None,
                    new=value.value,
                )
            )
            record.general[name] = value

    for entry in patch.coverages:
        match, is_ambiguous = match_base(entry, record.coverages)
        if is_ambiguous:
            ambiguous.append(entry.id)
            continue
        if match is None:
            new_id = entry.id if record.coverage(entry.id) is None else f"{entry.id}_a"
            added = entry.model_copy(update={"id": new_id})
            record.coverages.append(added)
            _place(record, added)
            changes.append(
                FieldChange(
                    path=f"coverages.{new_id}",
                    action="added",
                    new={"title": added.title, "category": added.category, "amount": added.amount},
                )
            )
            continue
        for field_name, value in entry.fields.items():
            if field_name == "title" or value.is_empty:
                continue
            old = match.fields.get(field_name)
            if _same(old, value):
                continue
            changes.append(
                FieldChange(
                    path=f"coverages.{match.id}.fields.{field_name}",
                    action="updated" if old else "added",
                    old=old.value if old else None,
                    new=value.value,
                )
            )
            match.fields[field_name] = value
        for attribute in ("amount", "currency"):
            new_value = getattr(entry, attribute)
            if new_value is not None and new_value != getattr(match, attribute):
                changes.append(
                    FieldChange(
                        path=f"coverages.{match.id}.{attribute}",
                        action="updated",
                        old=getattr(match, attribute),
                        new=new_value,
                    )
                )
                setattr(match, attribute, new_value)

    record.coverages = sort_entries(record.coverages)
    return ReconcileResult(record=record, changes=changes, ambiguous=ambiguous)


def diff_records(old: CoverageRecord, new: CoverageRecord) -> list[FieldChange]:
    """Field-level history between two full records (used on full republish)."""
    changes: list[FieldChange] = []
    for name in sorted(set(old.general) | set(new.general)):
        before, after = old.general.get(name), new.general.get(name)
        if not _same(before, after):
            action: Literal["added", "updated", "removed"] = (
                "added" if before is None else "removed" if after is None else "updated"
            )
            changes.append(
                FieldChange(
                    path=f"general.{name}",
                    action=action,
                    old=before.value if before else None,
                    new=after.value if after else None,
                )
            )
    old_items = {entry.id: entry for entry in old.coverages}
    new_items = {entry.id: entry for entry in new.coverages}
    for item_id in sorted(set(old_items) | set(new_items)):
        before_entry, after_entry = old_items.get(item_id), new_items.get(item_id)
        if before_entry is None and after_entry is not None:
            changes.append(
                FieldChange(path=f"coverages.{item_id}", action="added", new=after_entry.title)
            )
        elif after_entry is None and before_entry is not None:
            changes.append(
                FieldChange(path=f"coverages.{item_id}", action="removed", old=before_entry.title)
            )
        elif before_entry is not None and after_entry is not None:
            for attribute in ENTRY_ATTRIBUTES:
                was, now = getattr(before_entry, attribute), getattr(after_entry, attribute)
                if was != now:
                    changes.append(
                        FieldChange(
                            path=f"coverages.{item_id}.{attribute}",
                            action="updated",
                            old=was,
                            new=now,
                        )
                    )
            for field_name in sorted(set(before_entry.fields) | set(after_entry.fields)):
                a, b = before_entry.fields.get(field_name), after_entry.fields.get(field_name)
                if not _same(a, b):
                    changes.append(
                        FieldChange(
                            path=f"coverages.{item_id}.fields.{field_name}",
                            action="added" if a is None else "removed" if b is None else "updated",
                            old=a.value if a else None,
                            new=b.value if b else None,
                        )
                    )
    return changes
