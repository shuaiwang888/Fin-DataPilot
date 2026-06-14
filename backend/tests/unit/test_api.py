"""End-to-end FastAPI tests using httpx.AsyncClient."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_health(client) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tools"]["count"] == 4
    assert "financial-query" in body["tools"]["names"]


async def test_list_skills(client) -> None:
    r = await client.get("/api/skills")
    assert r.status_code == 200
    skills = r.json()["skills"]
    assert any(s["spec"]["name"] == "financial-query" for s in skills)
    assert all("enabled" in s for s in skills)


async def test_toggle_skill(client) -> None:
    # Disable announcement-search
    r = await client.patch(
        "/api/skills/announcement-search", json={"enabled": False}
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    # Re-enable
    r = await client.patch(
        "/api/skills/announcement-search", json={"enabled": True}
    )
    assert r.json()["enabled"] is True


async def test_create_and_list_session(client) -> None:
    r = await client.post("/api/sessions", json={"title": "测试"})
    assert r.status_code == 200
    sid = r.json()["id"]
    r = await client.get("/api/sessions")
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json()["sessions"])


async def test_get_nonexistent_session(client) -> None:
    r = await client.get("/api/sessions/does-not-exist")
    assert r.status_code == 404
