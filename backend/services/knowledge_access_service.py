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
        reuse_session: bool = True,
    ) -> List[Dict[str, Any]]:
        """Retrieve clauses for a section via OCI Agent Runtime and normalize output."""
        retrieval_query = self.build_retrieval_query(section_name=section_name, filters=filters, intake=intake)
        prompt = self._build_prompt(retrieval_query=retrieval_query, top_k=top_k)
        session_id = self._session_id if reuse_session else None
        try:
            response = self.rag_service.chat(message=prompt, session_id=session_id)
        except Exception as exc:
            logger.error("RAG retrieval failed for section=%s: %s", section_name, exc)
            return []

        session_id = response.get("session_id") or session_id
        if reuse_session:
            self._session_id = session_id
        candidates = self._extract_candidates(response)

        # If strict prompt yields no usable candidates, run one relaxed retry
        # to recover references from less-structured responses.
        if not candidates and allow_relaxed_retry:
            relaxed_query = self.build_retrieval_query(section_name=section_name, filters=filters, intake=intake, relax_tags=True)
            retry_prompt = self._build_relaxed_prompt(retrieval_query=relaxed_query, top_k=top_k)
            try:
                retry_response = self.rag_service.chat(message=retry_prompt, session_id=session_id)
                session_id = retry_response.get("session_id") or session_id
                if reuse_session:
                    self._session_id = session_id
                candidates = self._extract_candidates(retry_response)
                response = retry_response
            except Exception as exc:
                logger.warning("Relaxed RAG retry failed for section=%s: %s", section_name, exc)

        logger.info(
            "RAG retrieval diagnostics | section=%s | session=%s | answer_chars=%s | citations=%s | raw_candidates=%s",
            section_name,
            session_id,
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
                        "tags": metadata.get("tags") or filters.get("tags", []),
                        "risk_level": metadata.get("risk_level") or filters.get("risk_level", ["medium"]),
                    },
                }
            )

        logger.info("Knowledge retrieval returned %s candidates for section %s", len(normalized), section_name)
        return normalized


    def preview_section_quality(
        self,
        section_name: str,
        filters: Dict[str, Any],
        intake: Dict[str, Any],
        top_k: int = 5,
        include_relaxed: bool = True,
        reuse_session: bool = True,
    ) -> Dict[str, Any]:
        """Run strict (and optional relaxed) retrieval diagnostics for one section."""
        strict_query = self.build_retrieval_query(section_name=section_name, filters=filters, intake=intake)
        prompt = self._build_prompt(retrieval_query=strict_query, top_k=top_k)
        session_id = self._session_id if reuse_session else None
        try:
            strict_response = self.rag_service.chat(message=prompt, session_id=session_id)
        except Exception as exc:
            logger.error("RAG quality strict retrieval failed for section=%s: %s", section_name, exc)
            strict_response = {"answer": "", "citations": [], "session_id": session_id}
        session_id = strict_response.get("session_id") or session_id
        if reuse_session:
            self._session_id = session_id

        strict_candidates = self._extract_candidates(strict_response)
        strict_report = self._build_quality_report(
            mode="strict",
            response=strict_response,
            candidates=strict_candidates,
            section_name=section_name,
        )

        relaxed_report = None
        if include_relaxed:
            relaxed_query = self.build_retrieval_query(section_name=section_name, filters=filters, intake=intake, relax_tags=True)
            relaxed_prompt = self._build_relaxed_prompt(retrieval_query=relaxed_query, top_k=top_k)
            try:
                relaxed_response = self.rag_service.chat(message=relaxed_prompt, session_id=session_id)
            except Exception as exc:
                logger.warning("RAG quality relaxed retrieval failed for section=%s: %s", section_name, exc)
                relaxed_response = {"answer": "", "citations": [], "session_id": session_id}
            session_id = relaxed_response.get("session_id") or session_id
            if reuse_session:
                self._session_id = session_id
            relaxed_candidates = self._extract_candidates(relaxed_response)
            relaxed_report = self._build_quality_report(
                mode="relaxed",
                response=relaxed_response,
                candidates=relaxed_candidates,
                section_name=section_name,
            )

        return {
            "section": section_name,
            "session_id": session_id,
            "strict": strict_report,
            "relaxed": relaxed_report,
            "quality_summary": self._summarize_quality(strict_report, relaxed_report),
        }

    @staticmethod
    def _build_quality_report(
        mode: str,
        response: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        section_name: str,
    ) -> Dict[str, Any]:
        citations = (response or {}).get("citations", []) or []
        answer = (response or {}).get("answer", "")
        return {
            "mode": mode,
            "answer_preview": answer[:240],
            "answer_chars": len(answer),
            "citations_count": len(citations),
            "candidate_count": len(candidates),
            "has_json_candidates": KnowledgeAccessService._parse_candidates_from_answer(answer) is not None,
            "candidate_sample": [
                {
                    "chunk_id": item.get("chunk_id"),
                    "source_uri": item.get("source_uri"),
                    "score": item.get("score"),
                    "clause_text_preview": str(item.get("clause_text", ""))[:160],
                    "metadata": {
                        "section": (item.get("metadata") or {}).get("section", section_name),
                        "tags": (item.get("metadata") or {}).get("tags"),
                        "risk_level": (item.get("metadata") or {}).get("risk_level"),
                    },
                }
                for item in candidates[:3]
            ],
        }

    @staticmethod
    def _summarize_quality(strict_report: Dict[str, Any], relaxed_report: Optional[Dict[str, Any]]) -> str:
        strict_count = strict_report.get("candidate_count", 0)
        relaxed_count = (relaxed_report or {}).get("candidate_count", 0)
        strict_citations = strict_report.get("citations_count", 0)

        if strict_count > 0:
            return "strict_has_candidates"
        if relaxed_count > 0:
            return "strict_empty_relaxed_has_candidates"
        if strict_citations > 0:
            return "citations_present_but_no_candidates"
        return "likely_no_relevant_retrieval_or_prompt_mismatch"

    @staticmethod
    def _build_prompt(retrieval_query: Dict[str, Any], top_k: int) -> str:
        return (
            "Retrieve SoW clauses from the knowledge base using only the structured retrieval query. "
            "Do NOT use any freeform intake text. "
            "Return strict JSON only (no markdown, no prose) with top-level key 'candidates'. "
            "Each candidate object MUST include: chunk_id, source_uri, score, clause_text, and metadata {section, tags, risk_level}.\n"
            "Rules: "
            "(1) Respect every filter in retrieval_query. "
            "(2) clause_text MUST be non-empty and contain the clause body. "
            "(3) If no match exists, return {\"candidates\": []}. "
            "(4) Do not invent source_uri values.\n"
            f"TopK: {top_k}\n"
            f"retrieval_query: {json.dumps(retrieval_query)}\n"
            'Output schema example: {"candidates":[{"chunk_id":"...","source_uri":"...","score":0.91,"clause_text":"...","metadata":{"section":"Architecture","tags":["oci"],"risk_level":["medium"]}}]}'
        )

    @staticmethod
    def _build_relaxed_prompt(retrieval_query: Dict[str, Any], top_k: int) -> str:
        return (
            "Retrieve SoW clauses from the knowledge base for the structured retrieval query. "
            "Use only filter metadata and do not use freeform intake text. "
            "If strict JSON is not possible, still return best-effort candidates as JSON. "
            "Include clause_text whenever available and include citations/URIs if clause text is missing.\n"
            f"TopK: {top_k}\n"
            f"retrieval_query: {json.dumps(retrieval_query)}\n"
            "Do not answer with uncertainty text; always emit candidates array (possibly empty). "
            "Output JSON with key candidates."
        )


    def build_retrieval_query(
        self,
        section_name: str,
        filters: Dict[str, Any],
        intake: Dict[str, Any],
        relax_tags: bool = False,
    ) -> Dict[str, Any]:
        query = {"section": section_name, **(filters or {})}
        query.setdefault("industry", intake.get("industry"))
        query.setdefault("region", intake.get("region"))
        query.setdefault("deployment_model", intake.get("deployment_model"))
        query.setdefault("architecture_type", intake.get("architecture_pattern"))
        query.setdefault("compliance", intake.get("regulatory_context") or intake.get("compliance"))
        query.setdefault("cloud_provider", intake.get("cloud_provider"))
        query.setdefault("data_isolation_model", intake.get("data_isolation_model"))

        normalized: Dict[str, Any] = {}
        for key in self.FILTER_DIMENSIONS:
            value = query.get(key)
            if relax_tags and key == "tags":
                continue
            if value in (None, "", []):
                continue
            normalized[key] = value

        if "section" not in normalized:
            normalized["section"] = section_name
        return normalized

    @staticmethod
    def _extract_candidates(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        answer_text = (response or {}).get("answer", "")
        if not answer_text:
            return KnowledgeAccessService._candidates_from_citations(response)

        parsed_candidates = KnowledgeAccessService._parse_candidates_from_answer(answer_text)
        if parsed_candidates is not None:
            if parsed_candidates:
                return parsed_candidates
            citation_candidates = KnowledgeAccessService._candidates_from_citations(response)
            if citation_candidates:
                return citation_candidates
            return KnowledgeAccessService._candidates_from_answer_uris(answer_text)

        logger.warning("Could not parse retrieval response as JSON. Answer preview=%s", answer_text[:160])

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
    def _parse_candidates_from_answer(answer_text: str) -> Optional[List[Dict[str, Any]]]:
        # 1) Try direct parse.
        parsed = KnowledgeAccessService._safe_json_loads(answer_text.strip())
        extracted = KnowledgeAccessService._extract_candidates_from_parsed(parsed)
        if extracted is not None:
            return extracted

        # 2) Try fenced JSON blocks.
        for block in re.findall(r"```(?:json)?\s*(.*?)\s*```", answer_text, re.DOTALL):
            parsed = KnowledgeAccessService._safe_json_loads(block.strip())
            extracted = KnowledgeAccessService._extract_candidates_from_parsed(parsed)
            if extracted is not None:
                return extracted

        # 3) Try grabbing inline object/list snippets that include candidates.
        for snippet in re.findall(r"(\{[\s\S]*?\}|\[[\s\S]*?\])", answer_text):
            if "candidates" not in snippet and not snippet.strip().startswith("["):
                continue
            parsed = KnowledgeAccessService._safe_json_loads(snippet.strip())
            extracted = KnowledgeAccessService._extract_candidates_from_parsed(parsed)
            if extracted is not None:
                return extracted

        return None

    @staticmethod
    def _safe_json_loads(raw: str) -> Any:
        try:
            return json.loads(raw)
        except Exception:
            return None

    @staticmethod
    def _extract_candidates_from_parsed(parsed: Any) -> Optional[List[Dict[str, Any]]]:
        if isinstance(parsed, dict):
            if "candidates" in parsed:
                return parsed.get("candidates", []) or []
            return None

        if isinstance(parsed, list):
            dict_entries = [item for item in parsed if isinstance(item, dict)]
            if dict_entries:
                return dict_entries
        return None

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
    FILTER_DIMENSIONS = [
        "section",
        "clause_type",
        "tags",
        "risk_level",
        "industry",
        "region",
        "deployment_model",
        "architecture_type",
        "compliance",
        "architecture_pattern",
        "service_family",
        "cloud_provider",
        "data_isolation_model",
    ]
