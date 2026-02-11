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
        self._session_id: Optional[str] = None

    def retrieve_section_clauses(
        self,
        section_name: str,
        filters: Dict[str, Any],
        intake: Dict[str, Any],
        top_k: int = 5,
        allow_relaxed_retry: bool = True,
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
        response = self.rag_service.chat(message=prompt, session_id=self._session_id)
        self._session_id = response.get("session_id") or self._session_id
        candidates = self._extract_candidates(response)

        # If strict prompt yields no usable candidates, run one relaxed retry
        # to recover references from less-structured responses.
        if not candidates and allow_relaxed_retry:
            retry_prompt = self._build_relaxed_prompt(
                section_name=section_name,
                filters=filters,
                intake=intake,
                top_k=top_k,
            )
            retry_response = self.rag_service.chat(message=retry_prompt, session_id=self._session_id)
            self._session_id = retry_response.get("session_id") or self._session_id
            candidates = self._extract_candidates(retry_response)
            response = retry_response

        logger.info(
            "RAG retrieval diagnostics | section=%s | session=%s | answer_chars=%s | citations=%s | raw_candidates=%s",
            section_name,
            self._session_id,
            len((response or {}).get("answer", "")),
            len((response or {}).get("citations", []) or []),
            len(candidates),
        )

        if not candidates:
            logger.warning(
                "No normalized candidates for section=%s. Raw answer preview=%s",
                section_name,
                (response or {}).get("answer", "")[:240],
            )

        normalized: List[Dict[str, Any]] = []
        for idx, candidate in enumerate(candidates[:top_k], start=1):
            metadata = candidate.get("metadata") or {}
            normalized.append(
                {
                    "chunk_id": candidate.get("chunk_id") or f"{section_name.lower()}-kb-{idx}",
                    "source_uri": candidate.get("source_uri") or "unknown://source",
                    "score": float(candidate.get("score", 0.5)),
                    "clause_text": (
                        candidate.get("clause_text")
                        or candidate.get("text")
                        or candidate.get("content")
                        or candidate.get("summary")
                        or ""
                    ),
                    "metadata": {
                        # Force section scoping at normalization time so downstream
                        # validation is deterministic even if RAG returns a variant
                        # section label (e.g., casing/spacing differences).
                        "section": section_name,
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
            "Return strict JSON only (no markdown, no prose) with top-level key 'candidates'. "
            "Each candidate object MUST include: chunk_id, source_uri, score, clause_text, and metadata {section, clause_type, risk_level}.\n"
            "Rules: "
            "(1) metadata.section MUST exactly match the requested Section string. "
            "(2) clause_text MUST be non-empty and contain the actual clause body (not a title). "
            "(3) If no match exists, return {\"candidates\": []}. "
            "(4) Do not invent source_uri values.\n"
            f"Section: {section_name}\n"
            f"TopK: {top_k}\n"
            f"Filters: {json.dumps(filters)}\n"
            f"Client Context: {json.dumps({'industry': intake.get('industry'), 'region': intake.get('region'), 'document_type': intake.get('document_type')})}\n"
            "Output schema example: {\"candidates\":[{\"chunk_id\":\"...\",\"source_uri\":\"...\",\"score\":0.91,\"clause_text\":\"...\",\"metadata\":{\"section\":\""
            + section_name
            + "\",\"clause_type\":\"scope\",\"risk_level\":\"medium\"}}]}"
        )

    @staticmethod
    def _build_relaxed_prompt(section_name: str, filters: Dict[str, Any], intake: Dict[str, Any], top_k: int) -> str:
        return (
            "Retrieve SoW clauses from the knowledge base for the requested section. "
            "If strict JSON is not possible, still return best-effort candidates as JSON. "
            "Include clause_text whenever available and include citations/URIs if clause text is missing.\n"
            f"Section: {section_name}\n"
            f"TopK: {top_k}\n"
            f"Filters: {json.dumps(filters)}\n"
            f"Client Context: {json.dumps({'industry': intake.get('industry'), 'region': intake.get('region'), 'document_type': intake.get('document_type')})}\n"
            "Output JSON with key candidates."
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
                parsed_candidates = parsed.get("candidates", []) or []
                if parsed_candidates:
                    return parsed_candidates
                # If strict JSON response has empty list, still try citations/URIs.
                citation_candidates = KnowledgeAccessService._candidates_from_citations(response)
                if citation_candidates:
                    return citation_candidates
                return KnowledgeAccessService._candidates_from_answer_uris(answer_text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            logger.warning("Could not parse retrieval response as JSON")

        # Fall back to citation-derived candidates when the answer is not strict JSON.
        citation_candidates = KnowledgeAccessService._candidates_from_citations(response)
        if citation_candidates:
            return citation_candidates

        # Final fallback: extract URI-like references from free-form answer text.
        uri_candidates = KnowledgeAccessService._candidates_from_answer_uris(answer_text)
        if uri_candidates:
            logger.info("Using %s URI-derived candidates from answer text", len(uri_candidates))
        return uri_candidates

    @staticmethod
    def _candidates_from_citations(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        citations = (response or {}).get("citations", []) or []
        candidates: List[Dict[str, Any]] = []

        for idx, citation in enumerate(citations, start=1):
            source_uri = KnowledgeAccessService._citation_source_uri(citation)
            clause_text = KnowledgeAccessService._citation_clause_text(citation)
            if not source_uri and not clause_text:
                continue
            candidates.append(
                {
                    "chunk_id": f"citation-{idx}",
                    "source_uri": source_uri or f"citation://{idx}",
                    "score": 0.5,
                    "clause_text": clause_text,
                    "metadata": {},
                }
            )

        if candidates:
            logger.info("Using %s citation-derived candidates due to non-JSON RAG answer", len(candidates))

        return candidates

    @staticmethod
    def _citation_source_uri(citation: Any) -> str:
        if isinstance(citation, dict):
            source_location = citation.get("source_location") or {}
            return (
                str(source_location.get("url") or "")
                or str(citation.get("source_uri") or "")
                or str(citation.get("doc_id") or "")
                or ""
            )
        return str(citation or "")

    @staticmethod
    def _citation_clause_text(citation: Any) -> str:
        if isinstance(citation, dict):
            source_text = str(citation.get("source_text") or "").strip()
            if source_text:
                return KnowledgeAccessService._strip_front_matter(source_text)
            title = str(citation.get("title") or "").strip()
            if title:
                return title

        text = str(citation or "").strip()
        if not text:
            return ""
        return f"Evidence reference from citation source {text}."

    @staticmethod
    def _strip_front_matter(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("---"):
            parts = stripped.split("---", 2)
            if len(parts) == 3:
                stripped = parts[2].strip()
        return re.sub(r"\s+", " ", stripped).strip()

    @staticmethod
    def _candidates_from_answer_uris(answer_text: str) -> List[Dict[str, Any]]:
        uri_pattern = re.compile(r"(oci://[^\s,;]+|https?://[^\s,;]+)")
        uris = uri_pattern.findall(answer_text or "")
        unique_uris = list(dict.fromkeys(uris))

        candidates: List[Dict[str, Any]] = []
        for idx, source_uri in enumerate(unique_uris, start=1):
            candidates.append(
                {
                    "chunk_id": f"uri-{idx}",
                    "source_uri": source_uri,
                    "score": 0.4,
                    "clause_text": f"Evidence reference derived from answer URI {source_uri}.",
                    "metadata": {},
                }
            )

        return candidates
