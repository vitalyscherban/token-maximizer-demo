import pytest
from fastapi.testclient import TestClient

from tokenmax.api import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["provider"] == "mock"


def test_techniques_listed(client: TestClient):
    names = {t["name"] for t in client.get("/api/techniques").json()["techniques"]}
    assert {"compaction", "pruning", "cache"} <= names


def test_chat_returns_answer_and_savings(client: TestClient):
    body = client.post(
        "/api/chat", json={"session_id": "api1", "message": "What are your pricing tiers?"}
    ).json()
    assert body["answer"]
    assert body["baseline_tokens"] >= body["optimized_tokens"]


def test_empty_message_rejected(client: TestClient):
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422


def test_report_and_transcript(client: TestClient):
    client.post("/api/chat", json={"session_id": "api2", "message": "pricing tiers?"})
    report = client.get("/api/sessions/api2/report").json()
    assert report["model_calls"] > 0

    transcript = client.get("/api/sessions/api2/transcript").json()
    assert len(transcript["full_transcript"]) >= len(transcript["working_context"])


def test_unknown_session_is_404(client: TestClient):
    assert client.get("/api/sessions/nope/report").status_code == 404


def test_compare_shows_optimized_wins(client: TestClient):
    body = client.post("/api/compare", json={}).json()
    assert body["naive_tokens"] > body["optimized_tokens"]
    assert body["savings_pct"] > 0


def test_dashboard_served(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "tokenmax" in response.text
