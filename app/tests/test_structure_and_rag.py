"""Tests for deterministic structure and section-aware RAG."""

from __future__ import annotations

from app.agents.structure_controller import CANONICAL_STRUCTURE, StructureController
from app.services.rag_service import SectionAwareRAGService, SectionChunk


def test_structure_controller_returns_canonical_order(tmp_path) -> None:
    templates = tmp_path / "templates"
    static = templates / "static_sections"
    static.mkdir(parents=True)
    (static / "disclaimer.md").write_text("d", encoding="utf-8")
    (static / "generic_oci_explanations.md").write_text("g", encoding="utf-8")

    controller = StructureController(template_root=templates)

    assert controller.sections() == CANONICAL_STRUCTURE


def test_rag_retrieves_section_filtered_chunks() -> None:
    service = SectionAwareRAGService(
        chunks=[
            SectionChunk(section="FUTURE STATE ARCHITECTURE", text="Uses OKE and MySQL", client="A"),
            SectionChunk(section="SECURITY", text="IAM policies", client="A"),
            SectionChunk(section="FUTURE STATE ARCHITECTURE", text="Streaming pipelines", client="B"),
        ],
        top_k=2,
    )

    results = service.retrieve_section_context(
        section="FUTURE STATE ARCHITECTURE",
        project_data={"client": "A", "services": ["OKE"]},
    )

    assert len(results) == 2
    assert all(item.section == "FUTURE STATE ARCHITECTURE" for item in results)
    assert results[0].client == "A"
