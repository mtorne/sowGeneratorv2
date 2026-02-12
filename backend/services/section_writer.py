from __future__ import annotations

import json
from typing import Any, Dict, List

from services.knowledge_access_service import KnowledgeAccessService
from services.oci_client import OCIGenAIService


class SectionWriter:
    """Generates enterprise section content with precedence: extracted architecture > project data > RAG examples."""

    def __init__(
        self,
        oci_service: OCIGenAIService | None = None,
        knowledge_service: KnowledgeAccessService | None = None,
    ) -> None:
        self.oci_service = oci_service or OCIGenAIService()
        self.knowledge_service = knowledge_service or KnowledgeAccessService()

    def write_section(
        self,
        section_name: str,
        project_data: Dict[str, Any],
        architecture_context: Dict[str, Any],
        rag_context: List[Dict[str, Any]],
        llm_provider: str,
    ) -> str:
        rag_snippets = [entry.get("clause_text", "") for entry in rag_context if isinstance(entry, dict)]
        prompt = self._build_prompt(section_name, project_data, architecture_context, rag_snippets)
        return self.oci_service.generate_text_content(prompt=prompt, provider="generic", model_id=llm_provider)

    def retrieve_rag_context(self, section_name: str, project_data: Dict[str, Any], top_k: int = 4) -> List[Dict[str, Any]]:
        filters = {
            "section": section_name,
            "industry": project_data.get("industry"),
            "region": project_data.get("region"),
            "deployment_model": project_data.get("delivery_model"),
            "tags": ["sow", "architecture", section_name.lower()],
        }
        return self.knowledge_service.retrieve_section_clauses(
            section_name=section_name,
            filters=filters,
            intake=project_data,
            top_k=top_k,
            allow_relaxed_retry=True,
        )

    def _build_prompt(
        self,
        section_name: str,
        project_data: Dict[str, Any],
        architecture_context: Dict[str, Any],
        rag_snippets: List[str],
    ) -> str:
        return (
            "You are an Oracle consulting SoW writer. "
            "Write in professional enterprise tone and match historical SoW structure. "
            "Priority order: 1) extracted architecture context, 2) explicit project data, 3) RAG examples. "
            "Do not invent services not present in context. "
            f"Section: {section_name}\n"
            f"Project Data JSON: {json.dumps(project_data, ensure_ascii=False)}\n"
            f"Architecture Context JSON: {json.dumps(architecture_context, ensure_ascii=False)}\n"
            f"RAG Reference Snippets: {json.dumps(rag_snippets, ensure_ascii=False)}\n"
            "Return clean prose for this section only."
        )
