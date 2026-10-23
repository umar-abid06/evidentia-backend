from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

POLICY = {
    "code": "gold-travel",
    "name": "Gold Travel 2026",
    "display_name": "Gold Travel Cover",
    "insurer_name": "Example Insurance SE",
}


async def test_admin_manages_policies_and_cards(
    api: AsyncClient, admin_headers: dict[str, str]
) -> None:
    created = await api.post("/api/v1/admin/policies", json=POLICY, headers=admin_headers)
    assert created.status_code == 201
    policy_id = created.json()["id"]

    card = await api.post(
        "/api/v1/admin/card-products",
        json={
            "issuer_name": "Example Bank AG",
            "product_name": "Gold Visa",
            "display_name": "Example Gold",
            "country_code": "de",
            "policy_id": policy_id,
        },
        headers=admin_headers,
    )
    assert card.status_code == 201
    assert card.json()["country_code"] == "DE"

    listing = await api.get("/api/v1/admin/card-products?country_code=DE", headers=admin_headers)
    assert listing.json()["total"] == 1

    duplicate = await api.post("/api/v1/admin/policies", json=POLICY, headers=admin_headers)
    assert duplicate.status_code == 409


async def test_customers_cannot_reach_admin_routes(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    response = await api.post("/api/v1/admin/policies", json=POLICY, headers=customer_headers)
    assert response.status_code == 403
