"""Deterministic normalisation of model-extracted values."""

from __future__ import annotations

import re
from typing import Any

_CURRENCY_SYMBOLS: dict[str, str] = {
    "€": "EUR",
    "£": "GBP",
    "us$": "USD",
    "$": "USD",
    "¥": "JPY",
    "₹": "INR",
    "chf": "CHF",
    "sfr": "CHF",
    "aed": "AED",
    "pkr": "PKR",
}
_ISO_CURRENCY = re.compile(r"\b(EUR|USD|GBP|CHF|JPY|AED|SAR|PKR|INR|CNY|AUD|CAD|SEK|NOK|DKK|PLN)\b")
_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ ,.'\u202f]\d{3})+|\d+)(?:[.,](\d{1,2}))?(?![\d])")
_MULTIPLIER = re.compile(r"^\s*(m|mn|million|k|thousand|bn|billion)\b", re.IGNORECASE)
_MULTIPLIERS = {
    "m": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "k": 1e3,
    "thousand": 1e3,
    "bn": 1e9,
    "billion": 1e9,
}


def parse_currency(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    iso = _ISO_CURRENCY.search(text.upper())
    if iso:
        return iso.group(1)
    lowered = text.lower()
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in lowered:
            return code
    return None


def parse_amount(value: Any) -> float | None:
    """Parses '€1,000,000', 'EUR 1.000.000', '2 500 EUR', 'USD 5m', '250.50'."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value)
    match = _NUMBER.search(text)
    if match is None:
        return None
    whole = re.sub(r"[ ,.'\u202f]", "", match.group(1))
    number = float(whole)
    if match.group(2):
        number += float(f"0.{match.group(2)}")
    suffix = _MULTIPLIER.match(text[match.end() :])
    if suffix:
        number *= _MULTIPLIERS[suffix.group(1).lower()]
    return number


def amount_key(value: Any) -> str:
    """Comparable form of an amount for fingerprints and duplicate checks."""
    parsed = parse_amount(value)
    if parsed is not None:
        return f"{parsed:.2f}"
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()
