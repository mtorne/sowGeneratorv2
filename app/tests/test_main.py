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


def test_generate_sow_allows_known_services_when_service_list_not_provided(monkeypatch) -> None:
    """Guardrails should be skipped when request does not explicitly include services."""
    dynamic_sections = 11
    responses = iter(["Uses OKE, MySQL, and API Gateway."] * dynamic_sections + ["Reviewed full document."])

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
    }

    response = client.post("/generate-sow", json=payload)
    assert response.status_code == 200


def test_generate_sow_rejects_disallowed_services_when_service_list_is_explicit(monkeypatch) -> None:
    """Guardrails should reject generated services not present in explicit request services list."""
    dynamic_sections = 11
    responses = iter(["Uses OKE and Streaming."] * dynamic_sections + ["Reviewed full document."])

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
        "services": ["OKE"],
    }

    response = client.post("/generate-sow", json=payload)
    assert response.status_code == 422
    assert "Disallowed services in" in response.json()["detail"]


def test_inject_diagram_analysis_context_includes_confidence() -> None:
    from app.main import _inject_diagram_analysis_context

    section_content = "Base architecture narrative."
    context = {
        "architecture_analysis": {
            "current": {
                "file_name": "current-oke.png",
                "format": "png",
                "size_bytes": 1234,
                "inferred_components": ["OKE"],
                "analysis_confidence": "medium",
            }
        }
    }

    updated = _inject_diagram_analysis_context("CURRENT STATE ARCHITECTURE", section_content, context)

    assert "Diagram analysis evidence:" in updated
    assert "Current diagram analysis confidence: medium." in updated


def test_architecture_vision_agent_handles_empty_diagram() -> None:
    from app.agents.architecture_vision import ArchitectureVisionAgent

    analysis = ArchitectureVisionAgent().analyze(file_name="Picture1.png", content=b"", diagram_role="current")

    assert analysis["size_bytes"] == 0
    assert analysis["analysis_confidence"]["overall_confidence"] == "low"
    assert analysis["format"] == "unknown"
    assert analysis["architecture_extraction"]["error"]["code"] == "image_unreadable"
