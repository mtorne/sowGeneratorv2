from __future__ import annotations

import json
import logging
import re
import hashlib
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from oci import config, retry
from oci.object_storage import ObjectStorageClient

from services.oci_rag_client import OCIRAGService

logger = logging.getLogger(__name__)

MIN_CANDIDATE_TEXT_LEN = 40


def extract_candidates_from_agent_response(
    resp: Dict[str, Any],
    object_storage_client: Optional[ObjectStorageClient] = None,
    min_text_len: int = MIN_CANDIDATE_TEXT_LEN,
) -> List[Dict[str, Any]]:
    """Deterministically extract clause candidates from OCI Agent Runtime response citations.

    Primary source is citation.source_text. If absent, resolve citation source_uri to
    Object Storage and load text from the referenced chunk JSON.
    """
    citations = (resp or {}).get("citations", []) or []
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for idx, citation in enumerate(citations, start=1):
        citation_obj = citation if isinstance(citation, dict) else {"raw": str(citation)}
        source_location = citation_obj.get("source_location") or {}
        source_uri = (
            str(source_location.get("url") or "").strip()
            or str(citation_obj.get("source_uri") or "").strip()
            or str(citation_obj.get("doc_id") or citation_obj.get("documentId") or "").strip()
        )

        chunk_id = str(citation_obj.get("chunkId") or citation_obj.get("chunk_id") or "").strip()
        clause_id = chunk_id or (hashlib.sha256(source_uri.encode("utf-8")).hexdigest()[:16] if source_uri else f"citation-{idx}")
        dedupe_key = f"{clause_id}|{source_uri}"
        if dedupe_key in seen:
            continue

        metadata = citation_obj.get("metadata") if isinstance(citation_obj.get("metadata"), dict) else {}
        source_text = str(citation_obj.get("source_text") or "").strip()
        used_source_text = bool(source_text)
        text = source_text
        fetched_metadata: Dict[str, Any] = {}

        if not text and source_uri and object_storage_client is not None:
            chunk_payload = KnowledgeAccessService.fetch_object_storage_json(object_storage_client, source_uri)
            if isinstance(chunk_payload, dict):
                text = str(chunk_payload.get("text") or chunk_payload.get("clause") or "").strip()
                fetched_metadata = chunk_payload.get("metadata") if isinstance(chunk_payload.get("metadata"), dict) else {}

        text = KnowledgeAccessService._strip_front_matter(text)
        if len(text) < min_text_len:
            continue

        merged_metadata = {**fetched_metadata, **metadata}
        customized_url_source = merged_metadata.get("customized_url_source") or citation_obj.get("customized_url_source")
        if customized_url_source:
            merged_metadata["customized_url_source"] = customized_url_source

        candidates.append(
            {
                "clause_id": clause_id,
                "chunk_id": clause_id,
                "source_uri": source_uri,
                "document_id": citation_obj.get("doc_id") or citation_obj.get("documentId"),
                "title": citation_obj.get("title"),
                "clause_text": text,
                "text": text,
                "metadata": merged_metadata,
                "score": citation_obj.get("score") or citation_obj.get("similarity_score"),
                "retrieval_source": "citation_source_text" if used_source_text else "object_storage_fetch",
            }
        )
        seen.add(dedupe_key)

    return candidates


class KnowledgeAccessService:
    """Knowledge retrieval adapter with deterministic validation rules."""

    def __init__(self, rag_service: Optional[OCIRAGService] = None):
        self.rag_service = rag_service or OCIRAGService()
        self._session_id: Optional[str] = None
        self._object_storage_client: Optional[ObjectStorageClient] = None
        self._last_retrieval_diagnostics: Dict[str, Any] = {}

    def _get_object_storage_client(self) -> Optional[ObjectStorageClient]:
        if self._object_storage_client is not None:
            return self._object_storage_client
        try:
            self._object_storage_client = ObjectStorageClient(
                config=config.from_file(),
                retry_strategy=retry.NoneRetryStrategy(),
            )
            return self._object_storage_client
        except Exception as exc:
            logger.warning("ObjectStorageClient unavailable; citation fallback fetch disabled: %s", exc)
            return None

    def get_last_retrieval_diagnostics(self) -> Dict[str, Any]:
        return dict(self._last_retrieval_diagnostics)

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
        self._last_retrieval_diagnostics = {
            "citations_count": len((response or {}).get("citations", []) or []),
            "candidates_count": len(candidates),
            "used_source_text": sum(1 for c in candidates if c.get("retrieval_source") == "citation_source_text"),
            "used_fetched_object": sum(1 for c in candidates if c.get("retrieval_source") == "object_storage_fetch"),
        }

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

        self._last_retrieval_diagnostics = {
            "citations_count": len((response or {}).get("citations", []) or []),
            "candidates_count": len(candidates),
            "used_source_text": sum(1 for c in candidates if c.get("retrieval_source") == "citation_source_text"),
            "used_fetched_object": sum(1 for c in candidates if c.get("retrieval_source") == "object_storage_fetch"),
        }

        logger.info(
            "RAG retrieval diagnostics | section=%s | session=%s | answer_chars=%s | citations=%s | candidates=%s | source_text=%s | fetched_object=%s",
            section_name,
            session_id,
            len((response or {}).get("answer", "")),
            self._last_retrieval_diagnostics.get("citations_count", 0),
            self._last_retrieval_diagnostics.get("candidates_count", 0),
            self._last_retrieval_diagnostics.get("used_source_text", 0),
            self._last_retrieval_diagnostics.get("used_fetched_object", 0),
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

    def _extract_candidates(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        return extract_candidates_from_agent_response(
            response,
            object_storage_client=self._get_object_storage_client(),
            min_text_len=MIN_CANDIDATE_TEXT_LEN,
        )

    @staticmethod
    def parse_object_storage_uri(source_uri: str) -> Optional[Dict[str, str]]:
        raw = (source_uri or "").strip()
        if not raw.startswith("oci://objectstorage/"):
            return None
        path = raw[len("oci://objectstorage/") :]
        segments = [unquote(part) for part in path.split("/") if part]

        namespace = ""
        bucket = ""
        object_name = ""

        if "b" in segments and "o" in segments:
            b_idx = segments.index("b")
            o_idx = segments.index("o")
            if b_idx + 1 < len(segments):
                bucket = segments[b_idx + 1]
            if o_idx + 1 < len(segments):
                object_name = "/".join(segments[o_idx + 1 :])
            if "n" in segments:
                n_idx = segments.index("n")
                if n_idx + 1 < len(segments):
                    namespace = segments[n_idx + 1]
            elif b_idx > 0:
                namespace = segments[b_idx - 1]
        elif len(segments) >= 3:
            namespace, bucket = segments[0], segments[1]
            object_name = "/".join(segments[2:])

        if namespace and bucket and object_name:
            return {"namespace": namespace, "bucket": bucket, "object_name": object_name}
        return None

    @staticmethod
    def fetch_object_storage_json(object_storage_client: ObjectStorageClient, source_uri: str) -> Optional[Dict[str, Any]]:
        parsed = KnowledgeAccessService.parse_object_storage_uri(source_uri)
        if not parsed:
            return None
        try:
            obj = object_storage_client.get_object(
                namespace_name=parsed["namespace"],
                bucket_name=parsed["bucket"],
                object_name=parsed["object_name"],
            )
            payload = obj.data.content.decode("utf-8")
            parsed_json = json.loads(payload)
            return parsed_json if isinstance(parsed_json, dict) else None
        except Exception as exc:
            logger.warning("Failed to resolve citation object source_uri=%s: %s", source_uri, exc)
            return None

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
