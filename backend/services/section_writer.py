from __future__ import annotations

import json
from typing import Any, Dict

from services.knowledge_access_service import KnowledgeAccessService
from services.oci_client import OCIGenAIService
from services.rag_service import HybridRAGService


class SectionWriter:
    """Generates enterprise section content with precedence: extracted architecture > project data > RAG examples."""

    PRIORITY_SECTIONS = {
        "CURRENT STATE ARCHITECTURE",
        "FUTURE STATE ARCHITECTURE",
        "IMPLEMENTATION DETAILS",
        "ARCHITECTURE DEPLOYMENT OVERVIEW",
        "CURRENTLY USED TECHNOLOGY STACK",
        "OCI SERVICE SIZING",
    }

    def __init__(
        self,
        oci_service: OCIGenAIService | None = None,
        knowledge_service: KnowledgeAccessService | None = None,
        rag_service: HybridRAGService | None = None,
    ) -> None:
        self.oci_service = oci_service or OCIGenAIService()
        self.knowledge_service = knowledge_service or KnowledgeAccessService()
        self.rag_service = rag_service or HybridRAGService(self.knowledge_service)

    def write_section(
        self,
        section_name: str,
        project_data: Dict[str, Any],
        architecture_context: Dict[str, Any],
        rag_context: Dict[str, Any],
        llm_provider: str,
    ) -> str:
        rag_chunks = rag_context.get("chunks", []) if isinstance(rag_context, dict) else []
        prompt = self._build_prompt(section_name, project_data, architecture_context, rag_chunks)
        return self.oci_service.generate_text_content(prompt=prompt, provider="generic", model_id=llm_provider)

    def retrieve_rag_context(self, section_name: str, project_data: Dict[str, Any], architecture_context: Dict[str, Any], top_k: int = 4) -> Dict[str, Any]:
        return self.rag_service.retrieve_section_context(
            section_name=section_name,
            project_data=project_data,
            architecture_context=architecture_context,
            top_k=top_k,
        )

    def _build_prompt(
        self,
        section_name: str,
        project_data: Dict[str, Any],
        architecture_context: Dict[str, Any],
        rag_chunks: list[Dict[str, Any]],
    ) -> str:
        evidence_lines = self._architecture_evidence_lines(section_name, architecture_context)
        formatted_examples = "\n".join(
            [f"---\nExample {idx + 1}:\n{chunk.get('clause_text', '')}" for idx, chunk in enumerate(rag_chunks)]
        )

        section_guardrails = []
        if section_name.upper() in self.PRIORITY_SECTIONS:
            section_guardrails.append("Use architecture JSON first, then explicit project_data, then RAG examples.")
            section_guardrails.append("Never introduce OCI or third-party services absent from architecture_context and project_data.")
            section_guardrails.append("If OKE exists, describe OKE and node pools where visible.")
            section_guardrails.append("If WAF exists, describe WAF protection on ingress.")
            section_guardrails.append("If DRG or on-prem exists, describe DRG/VPN connectivity.")
            section_guardrails.append("If private endpoint/control plane appears, describe restricted endpoint access.")

        return (
            "You are an Oracle consulting SoW writer. "
            "Write in professional enterprise tone and deterministic structure. "
            "Do not hallucinate. "
            f"Section: {section_name}\n"
            f"Project Data JSON: {json.dumps(project_data, ensure_ascii=False)}\n"
            f"Architecture Context JSON: {json.dumps(architecture_context, ensure_ascii=False)}\n"
            f"Mandatory Architecture Evidence: {json.dumps(evidence_lines, ensure_ascii=False)}\n"
            f"Section Guardrails: {json.dumps(section_guardrails, ensure_ascii=False)}\n"
            "Reference Examples:\n"
            f"{formatted_examples}\n"
            "Reuse structure and phrasing patterns only. Do not copy examples verbatim. "
            "Return clean prose for this section only."
        )

    def _architecture_evidence_lines(self, section_name: str, architecture_context: Dict[str, Any]) -> list[str]:
        current = architecture_context.get("current_state", {})
        target = architecture_context.get("target_state", {})

        def names(items: Any) -> list[str]:
            out: list[str] = []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("name"):
                        out.append(str(item["name"]))
            return out

        lines: list[str] = []
        upper = section_name.upper()
        if "CURRENT STATE" in upper:
            lines.append(f"Current compute: {', '.join(names(current.get('compute'))) or 'not visible'}")
            lines.append(f"Current databases: {', '.join(names(current.get('databases'))) or 'not visible'}")
        elif "FUTURE STATE" in upper:
            lines.append(f"Target compute: {', '.join(names(target.get('compute'))) or 'not visible'}")
            lines.append(f"Target databases: {', '.join(names(target.get('databases'))) or 'not visible'}")
        elif "TECHNOLOGY STACK" in upper:
            stack = architecture_context.get("technology_stack", {})
            lines.append(f"Stack infra: {', '.join(stack.get('infrastructure', [])) or 'not visible'}")
            lines.append(f"Stack database: {', '.join(stack.get('database', [])) or 'not visible'}")
        else:
            lines.append(f"Networking components: {', '.join(names(target.get('networking')) + names(current.get('networking'))) or 'not visible'}")
            lines.append(f"Ingress/security components: {', '.join(names(target.get('load_balancers')) + names(target.get('security'))) or 'not visible'}")

        return lines
