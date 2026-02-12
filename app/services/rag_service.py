"""Section-aware retrieval service for SoW generation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


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
        """Retrieve top-k section-matched chunks with metadata-aware ranking."""
        cache_key = self._cache_key(section, project_data)
        if cache_key in self._cache:
            return self._cache[cache_key]

        section_filtered = [chunk for chunk in self._chunks if chunk.section.casefold() == section.casefold()]
        ranked = sorted(
            section_filtered,
            key=lambda chunk: self._score(chunk=chunk, project_data=project_data),
            reverse=True,
        )
        result = ranked[: self.top_k]
        self._cache[cache_key] = result
        return result

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
