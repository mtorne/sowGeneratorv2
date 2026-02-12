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
    """Generate endpoint should create docx and markdown outputs in app folder."""
    dynamic_sections = 11
    responses = iter(["Generated section content."] * dynamic_sections + ["Reviewed full document."])

    mock_call = lambda *_args, **_kwargs: next(responses)
    monkeypatch.setattr("app.agents.writer.call_llm", mock_call)
    monkeypatch.setattr("app.agents.qa.call_llm", mock_call)

    client = TestClient(app)
    payload = {
        "client": "Cegid",
        "project_name": "xrp Modernization",
        "cloud": "OCI",
        "scope": "Refactor monolith to microservices",
        "duration": "4 months",
        "services": ["OKE", "MySQL"],
    }

    response = client.post("/generate-sow", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "file" in body
    assert "markdown_file" in body
    assert body["file"].startswith("output_")
    assert body["file"].endswith(".docx")
    assert body["markdown_file"].startswith("output_")
    assert body["markdown_file"].endswith(".md")
    assert (Path("app") / body["file"]).exists()
    assert (Path("app") / body["markdown_file"]).exists()


def test_download_generated_files(monkeypatch) -> None:
    """Download endpoint should return generated docx and markdown files."""
    dynamic_sections = 11
    responses = iter(["Generated section content."] * dynamic_sections + ["Reviewed full document."])

    mock_call = lambda *_args, **_kwargs: next(responses)
    monkeypatch.setattr("app.agents.writer.call_llm", mock_call)
    monkeypatch.setattr("app.agents.qa.call_llm", mock_call)

    client = TestClient(app)
    payload = {
        "client": "Cegid",
        "project_name": "xrp Modernization",
        "cloud": "OCI",
        "scope": "Refactor monolith to microservices",
        "duration": "4 months",
        "services": ["OKE", "MySQL"],
    }

    generated = client.post("/generate-sow", json=payload).json()
    docx_response = client.get(f"/files/{generated['file']}")
    md_response = client.get(f"/files/{generated['markdown_file']}")

    assert docx_response.status_code == 200
    assert docx_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert md_response.status_code == 200
    assert md_response.headers["content-type"].startswith("text/markdown")


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
