"""CRM contact synchronisation (generic JSON webhook)."""

from __future__ import annotations

from typing import Any

import httpx


class CrmClient:
    def __init__(self, http: httpx.AsyncClient, url: str, api_key: str | None) -> None:
        self._http = http
        self._url = url
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def upsert_contact(self, contact: dict[str, Any]) -> None:
        response = await self._http.post(self._url, json=contact, headers=self._headers)
        response.raise_for_status()
