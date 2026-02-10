from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from services.oci_rag_client import OCIRAGService

logger = logging.getLogger(__name__)


class KnowledgeAccessService:
    """Knowledge retrieval adapter with deterministic validation rules."""

    def __init__(self, rag_service: Optional[OCIRAGService] = None):
        self.rag_service = rag_service or OCIRAGService()

    def retrieve_section_clauses(
        self,
        section_name: str,
        filters: Dict[str, Any],
        intake: Dict[str, Any],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve clauses for a section via OCI Agent Runtime and normalize output.
        Expected shape per clause:
          - chunk_id
          - source_uri
          - score
          - metadata: {section, clause_type, risk_level}
        """
        prompt = self._build_prompt(section_name=section_name, filters=filters, intake=intake, top_k=top_k)
        response = self.rag_service.chat(message=prompt)
        candidates = self._extract_candidates(response)

        normalized: List[Dict[str, Any]] = []
        for idx, candidate in enumerate(candidates[:top_k], start=1):
            metadata = candidate.get("metadata") or {}
            normalized.append(
                {
                    "chunk_id": candidate.get("chunk_id") or f"{section_name.lower()}-kb-{idx}",
                    "source_uri": candidate.get("source_uri") or "unknown://source",
                    "score": float(candidate.get("score", 0.5)),
                    "metadata": {
                        "section": metadata.get("section") or section_name,
                        "clause_type": metadata.get("clause_type") or filters.get("clause_type", "general"),
                        "risk_level": metadata.get("risk_level") or filters.get("risk_level", "medium"),
                    },
                }
            )

        logger.info("Knowledge retrieval returned %s candidates for section %s", len(normalized), section_name)
        return normalized

    @staticmethod
    def _build_prompt(section_name: str, filters: Dict[str, Any], intake: Dict[str, Any], top_k: int) -> str:
        return (
            "Retrieve SoW clauses from the knowledge base. "
            "Return JSON only with key 'candidates' as an array of objects. "
            "Each object must contain chunk_id, source_uri, score, and metadata {section, clause_type, risk_level}.\n"
            f"Section: {section_name}\n"
            f"TopK: {top_k}\n"
            f"Filters: {json.dumps(filters)}\n"
            f"Client Context: {json.dumps({'industry': intake.get('industry'), 'region': intake.get('region'), 'document_type': intake.get('document_type')})}\n"
            "Do not include prose explanation."
        )

    @staticmethod
    def _extract_candidates(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        answer_text = (response or {}).get("answer", "")
        if not answer_text:
            return KnowledgeAccessService._candidates_from_citations(response)

        # Try fenced JSON first.
        fenced = re.search(r"```json\s*(\{.*?\})\s*```", answer_text, re.DOTALL)
        raw_json = fenced.group(1) if fenced else answer_text.strip()

        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                return parsed.get("candidates", []) or []
            if isinstance(parsed, list):
                return parsed
        except Exception:
            logger.warning("Could not parse retrieval response as JSON")

        # Fall back to citation-derived candidates when the answer is not strict JSON.
        return KnowledgeAccessService._candidates_from_citations(response)

    @staticmethod
    def _candidates_from_citations(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        citations = (response or {}).get("citations", []) or []
        candidates: List[Dict[str, Any]] = []

        for idx, citation in enumerate(citations, start=1):
            source_uri = str(citation)
            candidates.append(
                {
                    "chunk_id": f"citation-{idx}",
                    "source_uri": source_uri,
                    "score": 0.5,
                    "metadata": {},
                }
            )

        if candidates:
            logger.info("Using %s citation-derived candidates due to non-JSON RAG answer", len(candidates))

        return candidates
