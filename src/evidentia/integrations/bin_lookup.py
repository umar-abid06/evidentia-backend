"""Card-range (BIN/IIN) lookup adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from evidentia.core.config import CardsSettings


class BinLookupUnavailableError(Exception):
    """The lookup service is down or rate limited; nothing may be cached or guessed."""


@dataclass(frozen=True, slots=True)
class BinRecord:
    issuer: str
    product_name: str
    scheme: str | None
    funding_type: str | None
    country_code: str | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


class BinLookup(Protocol):
    async def lookup(self, bin_number: str) -> list[BinRecord]: ...


def _parse(item: Mapping[str, Any]) -> BinRecord | None:
    bank = item.get("bank") or {}
    country = item.get("country") or {}
    issuer = item.get("issuer") or (bank.get("name") if isinstance(bank, Mapping) else None)
    if not issuer:
        return None
    country_code = item.get("country_code") or (
        country.get("alpha2") if isinstance(country, Mapping) else None
    )
    return BinRecord(
        issuer=str(issuer),
        product_name=str(
            item.get("product_name") or item.get("brand") or item.get("product") or ""
        ),
        scheme=item.get("scheme"),
        funding_type=(item.get("funding_type") or item.get("type") or None),
        country_code=str(country_code).upper() if country_code else None,
        raw=dict(item),
    )


class HttpBinLookup:
    """Works with providers that return one record or a list (binlist-style shape)."""

    def __init__(self, client: httpx.AsyncClient, config: CardsSettings) -> None:
        self._client = client
        self._url = (config.bin_lookup_url or "").rstrip("/")
        self._headers = (
            {"Authorization": f"Bearer {config.bin_lookup_api_key.get_secret_value()}"}
            if config.bin_lookup_api_key
            else {}
        )
        self._timeout = config.bin_lookup_timeout_s

    async def lookup(self, bin_number: str) -> list[BinRecord]:
        try:
            response = await self._client.get(
                f"{self._url}/{bin_number}", headers=self._headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise BinLookupUnavailableError(str(exc)) from exc
        if response.status_code == 404:
            return []
        if response.status_code == 429 or response.status_code >= 500:
            raise BinLookupUnavailableError(f"lookup returned {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        items = payload if isinstance(payload, list) else payload.get("results", [payload])
        return [record for item in items if isinstance(item, Mapping) and (record := _parse(item))]


class StaticBinLookup:
    """In-memory ranges for local development and tests."""

    def __init__(self, ranges: Mapping[str, list[BinRecord]] | None = None) -> None:
        self.ranges = dict(ranges or {})
        self.available = True

    async def lookup(self, bin_number: str) -> list[BinRecord]:
        if not self.available:
            raise BinLookupUnavailableError("static lookup disabled")
        for length in range(len(bin_number), 5, -1):
            if (found := self.ranges.get(bin_number[:length])) is not None:
                return found
        return []


def build_bin_lookup(config: CardsSettings, client: httpx.AsyncClient) -> BinLookup:
    if config.bin_lookup_url:
        return HttpBinLookup(client, config)
    return StaticBinLookup()
