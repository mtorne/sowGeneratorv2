"""Section-aware retrieval service for SoW generation."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionChunk:
    """Typed representation for a section chunk in vector/RAG corpus."""

    section: str
    text: str
    client: str = ""
    industry: str = ""
    services: tuple[str, ...] = ()


class SectionAwareRAGService:
    """Per-request retriever with section filtering and caching."""

    def __init__(self, chunks: list[SectionChunk], top_k: int = 3) -> None:
        self._chunks = chunks
        self.top_k = top_k
        self._cache: dict[str, list[SectionChunk]] = {}

    @classmethod
    def from_env(cls) -> "SectionAwareRAGService":
        """Initialize service from env-configured chunk source."""
        top_k = int(os.getenv("RAG_TOP_K", "3"))
        chunks_path = os.getenv("RAG_CHUNKS_PATH", "")
        chunks = _load_chunks(chunks_path) if chunks_path else []
        return cls(chunks=chunks, top_k=top_k)

    def retrieve_section_context(self, section: str, project_data: dict[str, Any]) -> list[SectionChunk]:
        """Retrieve top-k section-matched chunks with diagnostics and semantic fallback."""
        cache_key = self._cache_key(section, project_data)
        if cache_key in self._cache:
            return self._cache[cache_key]

        results = self._retrieve_by_section(section=section, project_data=project_data)
        self._cache[cache_key] = results
        return results

    def diagnose_vector_store(self) -> bool:
        """Run retrieval diagnostics to verify RAG corpus is loaded and queryable."""
        logger.info("rag.diagnostic_start")
        try:
            doc_count = self.count()
            logger.info("rag.diagnostic doc_count=%s", doc_count)
            if doc_count == 0:
                logger.error("rag.diagnostic_failed reason=empty_index")
                return False

            sample_chunks = self._chunks[:3]
            for chunk in sample_chunks:
                logger.info(
                    "rag.sample_chunk section=%s client=%s industry=%s preview=%s",
                    chunk.section,
                    chunk.client,
                    chunk.industry,
                    chunk.text[:100],
                )

            logger.info("rag.diagnostic_passed")
            return True
        except Exception:
            logger.exception("rag.diagnostic_exception")
            return False

    def refresh_from_env(self) -> int:
        """Reload chunks from configured source to ensure latest indexed documents are available."""
        chunks_path = os.getenv("RAG_CHUNKS_PATH", "")
        _load_chunks.cache_clear()
        self._chunks = _load_chunks(chunks_path) if chunks_path else []
        self._cache.clear()
        logger.info("rag.refresh_from_env chunk_count=%s", len(self._chunks))
        return len(self._chunks)

    def count(self) -> int:
        """Return corpus document count."""
        return len(self._chunks)

    def _build_semantic_query(self, section: str, project_data: dict[str, Any]) -> str:
        services = ", ".join(str(s) for s in project_data.get("services", []) if isinstance(s, str))
        return f"section={section}; client={project_data.get('client', '')}; industry={project_data.get('industry', '')}; services={services}"

    def _retrieve_by_section(self, section: str, project_data: dict[str, Any]) -> list[SectionChunk]:
        """Retrieve chunks with comprehensive diagnostics and section metadata fallback."""
        logger.info("rag.retrieve_start section=%s", section)

        try:
            total_docs = self.count()
            logger.info("rag.vector_store_total_docs count=%s", total_docs)
            if total_docs == 0:
                logger.error("rag.empty_vector_store - NO DOCUMENTS INDEXED")
                return []
        except Exception:
            logger.exception("rag.vector_store_check_failed")

        query = self._build_semantic_query(section=section, project_data=project_data)
        logger.info("rag.query section=%s query=%s", section, query[:100])

        try:
            ranked_all = sorted(
                self._chunks,
                key=lambda chunk: self._score(chunk=chunk, project_data=project_data),
                reverse=True,
            )
            all_results = ranked_all[: max(self.top_k * 4, 20)]
            logger.info("rag.unfiltered_results count=%s", len(all_results))

            if all_results:
                found_sections = sorted({r.section for r in all_results if r.section})
                logger.info("rag.available_sections sections=%s", found_sections)

            filtered_ranked = sorted(
                [chunk for chunk in all_results if chunk.section.casefold() == section.casefold()],
                key=lambda chunk: self._score(chunk=chunk, project_data=project_data),
                reverse=True,
            )
            logger.info("rag.filtered_results section=%s count=%s", section, len(filtered_ranked))

            if not filtered_ranked and all_results:
                logger.error("rag.metadata_mismatch requested_section=%s but_not_in_results=True", section)
                return all_results[: self.top_k]

            return filtered_ranked[: self.top_k] if filtered_ranked else []
        except Exception:
            logger.exception("rag.retrieve_failed section=%s", section)
            return []

    def _cache_key(self, section: str, project_data: dict[str, Any]) -> str:
        raw = json.dumps(project_data, sort_keys=True, ensure_ascii=False)
        return f"{section.casefold()}::{raw}"

    def _score(self, chunk: SectionChunk, project_data: dict[str, Any]) -> float:
        score = 0.0
        if chunk.client and chunk.client.casefold() == str(project_data.get("client", "")).casefold():
            score += 3.0
        if chunk.industry and chunk.industry.casefold() == str(project_data.get("industry", "")).casefold():
            score += 2.0

        req_services = {s.casefold() for s in project_data.get("services", []) if isinstance(s, str)}
        chunk_services = {s.casefold() for s in chunk.services}
        score += len(req_services.intersection(chunk_services)) * 1.5

        project_blob = _normalize_text(" ".join(str(v) for v in project_data.values()))
        chunk_blob = _normalize_text(chunk.text)
        project_tokens = set(project_blob.split())
        chunk_tokens = set(chunk_blob.split())
        overlap = len(project_tokens.intersection(chunk_tokens))
        return score + (overlap / max(len(project_tokens), 1))


@lru_cache(maxsize=4)
def _load_chunks(chunks_path: str) -> list[SectionChunk]:
    path = Path(chunks_path)
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else []
    chunks: list[SectionChunk] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        chunks.append(
            SectionChunk(
                section=str(row.get("section", "")).strip(),
                text=str(row.get("text", "")).strip(),
                client=str(row.get("client", "")).strip(),
                industry=str(row.get("industry", "")).strip(),
                services=tuple(str(item).strip() for item in row.get("services", []) if str(item).strip()),
            )
        )
    return [chunk for chunk in chunks if chunk.section and chunk.text]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9 ]", " ", text)).strip().casefold()
