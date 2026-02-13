from __future__ import annotations

import logging
from typing import Any, Dict, List

from services.knowledge_access_service import KnowledgeAccessService

logger = logging.getLogger(__name__)


class HybridRAGService:
    SECTION_ALIASES = {
        "FUTURE STATE ARCHITECTURE": ["Target Architecture", "Future Architecture"],
        "CURRENT STATE ARCHITECTURE": ["Existing Architecture", "As-Is Architecture"],
    }

    def __init__(self, knowledge_service: KnowledgeAccessService | None = None) -> None:
        self.knowledge_service = knowledge_service or KnowledgeAccessService()

    def retrieve_section_context(
        self,
        section_name: str,
        project_data: Dict[str, Any],
        architecture_context: Dict[str, Any],
        top_k: int = 4,
    ) -> Dict[str, Any]:
        strategies: List[str] = []
        filters = self._base_filters(section_name, project_data)

        candidates = self.knowledge_service.retrieve_section_clauses(
            section_name=section_name,
            filters=filters,
            intake=project_data,
            top_k=top_k,
            allow_relaxed_retry=True,
        )
        strategies.append("metadata")

        if not candidates:
            for alias in self.SECTION_ALIASES.get(section_name, []):
                alias_filters = self._base_filters(alias, project_data)
                alias_candidates = self.knowledge_service.retrieve_section_clauses(
                    section_name=alias,
                    filters=alias_filters,
                    intake=project_data,
                    top_k=top_k,
                    allow_relaxed_retry=True,
                )
                strategies.append(f"alias:{alias}")
                candidates.extend(alias_candidates)
                if candidates:
                    break

        if not candidates:
            semantic_query = f"{section_name} | architecture={architecture_context} | project_data={project_data}"
            semantic_filters = self._base_filters(section_name, project_data)
            semantic_filters["tags"] = ["sow", "architecture", "semantic", semantic_query[:120]]
            semantic_candidates = self.knowledge_service.retrieve_section_clauses(
                section_name=section_name,
                filters=semantic_filters,
                intake=project_data,
                top_k=max(top_k, 6),
                allow_relaxed_retry=True,
            )
            strategies.append("semantic")
            candidates.extend(semantic_candidates)

        deduped = self._dedupe(candidates)
        if len(deduped) < 3:
            deduped.extend(self._fallback_chunks(section_name, architecture_context, count=3 - len(deduped)))

        final = deduped[: max(3, top_k)]
        logger.info("RAG section=%s chunk_count=%s strategy=%s", section_name, len(final), " -> ".join(strategies))
        return {
            "chunks": final,
            "strategy": strategies,
            "chunk_count": len(final),
        }

    def _base_filters(self, section_name: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "section": section_name,
            "industry": project_data.get("industry"),
            "region": project_data.get("region"),
            "deployment_model": project_data.get("delivery_model"),
            "tags": ["sow", "architecture", section_name.lower()],
        }

    @staticmethod
    def _dedupe(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[str, Dict[str, Any]] = {}
        for chunk in chunks:
            key = str(chunk.get("chunk_id") or chunk.get("source_uri") or chunk.get("clause_text", "")[:80])
            if key and key not in deduped:
                deduped[key] = chunk
        return list(deduped.values())

    @staticmethod
    def _fallback_chunks(section_name: str, architecture_context: Dict[str, Any], count: int) -> List[Dict[str, Any]]:
        fallback = []
        for idx in range(count):
            fallback.append(
                {
                    "chunk_id": f"fallback-{section_name.lower().replace(' ', '-')}-{idx+1}",
                    "source_uri": "fallback://architecture-context",
                    "score": 0.1,
                    "clause_text": f"Use architecture_context facts only for {section_name}. Do not invent unsupported services.",
                    "metadata": {"section": section_name, "strategy": "fallback", "architecture_keys": list(architecture_context.keys())},
                }
            )
        return fallback
