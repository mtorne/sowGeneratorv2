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
        evidence_lines = self._architecture_evidence_lines(section_name, architecture_context)
        return (
            "You are an Oracle consulting SoW writer. "
            "Write in professional enterprise tone and match historical SoW structure. "
            "Priority order: 1) extracted architecture context, 2) explicit project data, 3) RAG examples. "
            "Do not invent services not present in context. "
            "Architecture evidence lines are mandatory: explicitly incorporate them where relevant. "
            f"Section: {section_name}\n"
            f"Project Data JSON: {json.dumps(project_data, ensure_ascii=False)}\n"
            f"Architecture Context JSON: {json.dumps(architecture_context, ensure_ascii=False)}\n"
            f"RAG Reference Snippets: {json.dumps(rag_snippets, ensure_ascii=False)}\n"
            f"Mandatory Architecture Evidence: {json.dumps(evidence_lines, ensure_ascii=False)}\n"
            "Return clean prose for this section only."
        )

    def _architecture_evidence_lines(self, section_name: str, architecture_context: Dict[str, Any]) -> List[str]:
        current = architecture_context.get("current_state", {})
        target = architecture_context.get("target_state", {})
        stack = architecture_context.get("technology_stack", {})

        def names(items: Any) -> List[str]:
            out: List[str] = []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("name"):
                        out.append(str(item["name"]))
            return out

        current_compute = names(current.get("compute"))
        current_db = names(current.get("databases"))
        target_compute = names(target.get("compute"))
        target_db = names(target.get("databases"))
        lbs = names(current.get("load_balancers")) + names(target.get("load_balancers"))

        lines: List[str] = []
        upper = section_name.upper()
        if "CURRENT STATE" in upper:
            lines.append(f"Current compute components: {', '.join(current_compute) if current_compute else 'not clearly visible in diagram'}")
            lines.append(f"Current database components: {', '.join(current_db) if current_db else 'not clearly visible in diagram'}")
        elif "FUTURE STATE" in upper:
            lines.append(f"Target compute components: {', '.join(target_compute) if target_compute else 'not clearly visible in diagram'}")
            lines.append(f"Target database components: {', '.join(target_db) if target_db else 'not clearly visible in diagram'}")
        elif "TECHNOLOGY STACK" in upper:
            db_stack = stack.get("database", []) if isinstance(stack.get("database"), list) else []
            infra_stack = stack.get("infrastructure", []) if isinstance(stack.get("infrastructure"), list) else []
            lines.append(f"Technology stack (database): {', '.join(db_stack) if db_stack else 'not clearly visible in diagram'}")
            lines.append(f"Technology stack (infrastructure): {', '.join(infra_stack) if infra_stack else 'not clearly visible in diagram'}")
        else:
            lines.append(f"Load balancers/ingress components: {', '.join(lbs) if lbs else 'none visible'}")
            lines.append(f"Target infrastructure components: {', '.join(target_compute) if target_compute else 'not clearly visible in diagram'}")

        if current.get("kubernetes", {}).get("present") or target.get("kubernetes", {}).get("present"):
            lines.append("Kubernetes detected in diagram context; reflect platform details consistently.")

        return lines
