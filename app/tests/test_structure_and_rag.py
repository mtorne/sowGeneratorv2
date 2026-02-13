"""Tests for deterministic structure and section-aware RAG."""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.structure_controller import CANONICAL_STRUCTURE, StructureController
from app.services.rag_service import SectionAwareRAGService


class FakeRuntimeClient:
    def __init__(self, documents):
        self._documents = documents

    def search_documents(self, agent_endpoint_id, search_documents_details):
        return SimpleNamespace(data=SimpleNamespace(documents=self._documents))


def test_structure_controller_returns_canonical_order(tmp_path) -> None:
    templates = tmp_path / "templates"
    static = templates / "static_sections"
    static.mkdir(parents=True)
    (static / "disclaimer.md").write_text("d", encoding="utf-8")
    (static / "generic_oci_explanations.md").write_text("g", encoding="utf-8")

    controller = StructureController(template_root=templates)

    assert controller.sections() == CANONICAL_STRUCTURE


def test_rag_retrieves_section_filtered_chunks() -> None:
    docs = [
        SimpleNamespace(
            text="Uses OKE and MySQL",
            metadata={"section": "FUTURE STATE ARCHITECTURE", "client": "A"},
        ),
        SimpleNamespace(text="IAM policies", metadata={"section": "SECURITY", "client": "A"}),
        SimpleNamespace(
            text="Streaming pipelines",
            metadata={"section": "FUTURE STATE ARCHITECTURE", "client": "B"},
        ),
    ]
    service = SectionAwareRAGService(
        oci_config={},
        agent_endpoint_id="ocid1.test.endpoint",
        knowledge_base_id="ocid1.test.kb",
        runtime_client=FakeRuntimeClient(docs),
        top_k=2,
    )
    service.count = lambda: len(docs)  # type: ignore[method-assign]

    results = service.retrieve_section_context(
        section="FUTURE STATE ARCHITECTURE",
        project_data={"client": "A", "services": ["OKE"]},
    )

    assert len(results) == 2
    assert all(item.section == "FUTURE STATE ARCHITECTURE" for item in results)
    assert results[0].client == "A"


def test_rag_falls_back_when_section_metadata_missing() -> None:
    docs = [
        SimpleNamespace(text="OKE cluster with WAF and IAM", metadata={"section": "SECURITY", "client": "A"}),
        SimpleNamespace(text="Compute and networking baseline", metadata={"section": "PROJECT OVERVIEW", "client": "A"}),
    ]
    service = SectionAwareRAGService(
        oci_config={},
        agent_endpoint_id="ocid1.test.endpoint",
        knowledge_base_id="ocid1.test.kb",
        runtime_client=FakeRuntimeClient(docs),
        top_k=1,
    )
    service.count = lambda: len(docs)  # type: ignore[method-assign]

    results = service.retrieve_section_context(
        section="FUTURE STATE ARCHITECTURE",
        project_data={"client": "A", "services": ["OKE"]},
    )

    assert len(results) == 1
    assert results[0].section == "SECURITY"


def test_rag_diagnose_vector_store_reports_empty_and_non_empty() -> None:
    empty_service = SectionAwareRAGService(
        oci_config={},
        agent_endpoint_id="ocid1.test.endpoint",
        knowledge_base_id="ocid1.test.kb",
        runtime_client=FakeRuntimeClient([]),
    )
    empty_service.count = lambda: 0  # type: ignore[method-assign]
    assert empty_service.diagnose_vector_store() is False

    non_empty_docs = [SimpleNamespace(text="IAM policy hardening", metadata={"section": "SECURITY"})]
    non_empty_service = SectionAwareRAGService(
        oci_config={},
        agent_endpoint_id="ocid1.test.endpoint",
        knowledge_base_id="ocid1.test.kb",
        runtime_client=FakeRuntimeClient(non_empty_docs),
    )
    non_empty_service.count = lambda: 1  # type: ignore[method-assign]
    assert non_empty_service.diagnose_vector_store() is True
