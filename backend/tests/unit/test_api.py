"""API contract tests: bearer isolation and operator endpoints."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.database


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def _identity(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/api/auth/anonymous")
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_health_is_minimal_and_public(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "llm" not in response.json()
    assert "tools" not in response.json()


async def test_protected_routes_require_bearer_token(client: AsyncClient) -> None:
    response = await client.get("/api/sessions")
    assert response.status_code == 401
    response = await client.get("/api/skills")
    assert response.status_code == 401


async def test_session_isolation(client: AsyncClient) -> None:
    first = await _identity(client)
    second = await _identity(client)
    created = await client.post("/api/sessions", json={"title": "私有会话"}, headers=first)
    assert created.status_code == 200
    session_id = created.json()["id"]

    own = await client.get("/api/sessions", headers=first)
    assert any(item["id"] == session_id for item in own.json()["sessions"])
    assert (await client.get(f"/api/sessions/{session_id}", headers=second)).status_code == 404
    assert (await client.delete(f"/api/sessions/{session_id}", headers=second)).status_code == 404
    assert (await client.get("/api/sessions", headers=second)).json()["sessions"] == []


async def test_client_cannot_choose_user_id(client: AsyncClient) -> None:
    headers = await _identity(client)
    response = await client.post(
        "/api/sessions", json={"title": "测试", "user_id": "somebody-else"}, headers=headers
    )
    assert response.status_code == 200
    session = await client.get(f"/api/sessions/{response.json()['id']}", headers=headers)
    assert session.status_code == 200
    assert "user_id" not in session.json()["session"]


async def test_anonymous_identity_can_renew_without_changing_user(client: AsyncClient) -> None:
    first = await client.post("/api/auth/anonymous")
    first_body = first.json()
    renewed = await client.post(
        "/api/auth/anonymous",
        headers={"Authorization": f"Bearer {first_body['access_token']}"},
    )
    assert renewed.status_code == 200
    assert renewed.json()["user_id"] == first_body["user_id"]


async def test_memory_api_is_isolated(client: AsyncClient) -> None:
    from app.storage.repository import upsert_long_term_memory_async

    first = await client.post("/api/auth/anonymous")
    first_body = first.json()
    first_headers = {"Authorization": f"Bearer {first_body['access_token']}"}
    second_headers = await _identity(client)
    session = await client.post("/api/sessions", json={"title": "memory"}, headers=first_headers)
    memory_id = await upsert_long_term_memory_async(
        first_body["user_id"], "preference", "偏好低风险", "risk:low", 5, session.json()["id"]
    )

    own = await client.get("/api/memories", headers=first_headers)
    assert [item["id"] for item in own.json()["memories"]] == [memory_id]
    assert (await client.get("/api/memories", headers=second_headers)).json()["memories"] == []
    assert (await client.delete(f"/api/memories/{memory_id}", headers=second_headers)).status_code == 404


async def test_skill_mutation_is_operator_only(client: AsyncClient) -> None:
    headers = await _identity(client)
    response = await client.patch(
        "/api/skills/announcement-search", json={"enabled": False}, headers=headers
    )
    assert response.status_code == 503
    response = await client.post("/api/skills/upload", headers={"X-API-Key": "wrong"})
    assert response.status_code in {403, 503, 422}
