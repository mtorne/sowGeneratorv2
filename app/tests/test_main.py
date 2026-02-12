"""Basic API tests for the SoW generator."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    """Health endpoint should return service status."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generate_sow_with_mock_llm(monkeypatch) -> None:
    """Generate endpoint should create a docx output in app folder."""
    responses = iter(
        [
            '{"sections": ["Executive Summary", "Scope"]}',
            "Executive summary content.",
            "Scope content.",
            "Reviewed full document.",
        ]
    )

    mock_call = lambda *_args, **_kwargs: next(responses)
    monkeypatch.setattr("app.agents.planner.call_llm", mock_call)
    monkeypatch.setattr("app.agents.writer.call_llm", mock_call)
    monkeypatch.setattr("app.agents.qa.call_llm", mock_call)

    client = TestClient(app)
    payload = {
        "client": "Cegid",
        "project_name": "xrp Modernization",
        "cloud": "OCI",
        "scope": "Refactor monolith to microservices",
        "duration": "4 months",
    }

    response = client.post("/generate-sow", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "file" in body
    assert body["file"].startswith("output_")
    assert body["file"].endswith(".docx")
    assert (Path("app") / body["file"]).exists()


def test_cors_preflight_health() -> None:
    """CORS preflight should be accepted for browser clients."""
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "Origin": "https://sowgen.enrot.es",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"
