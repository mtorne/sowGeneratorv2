"""Section-aware retrieval service for SoW generation."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import oci
from oci import retry
from oci.generative_ai_agent import GenerativeAiAgentClient
from oci.generative_ai_agent_runtime import GenerativeAiAgentRuntimeClient


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
    """RAG service that retrieves context using OCI Knowledge Base search."""

    def __init__(
        self,
        oci_config: dict[str, Any] | None,
        agent_endpoint_id: str,
        knowledge_base_id: str,
        top_k: int = 5,
        service_endpoint: str | None = None,
        runtime_client: Any | None = None,
    ) -> None:
        self._oci_config = oci_config or {}
        self.agent_endpoint_id = agent_endpoint_id
        self.knowledge_base_id = knowledge_base_id
        self.top_k = top_k
        self._cache: dict[str, list[SectionChunk]] = {}

        if runtime_client is not None:
            self.runtime_client = runtime_client
        else:
            self.runtime_client = GenerativeAiAgentRuntimeClient(
                config=self._oci_config,
                service_endpoint=service_endpoint,
                retry_strategy=retry.NoneRetryStrategy(),
                timeout=(
                    int(os.getenv("OCI_TIMEOUT_CONNECT", "10")),
                    int(os.getenv("OCI_TIMEOUT_READ", "240")),
                ),
            )

        logger.info(
            "rag.initialized agent_endpoint=%s kb=%s",
            agent_endpoint_id[-20:],
            knowledge_base_id[-20:],
        )

    @classmethod
    def from_env(cls) -> "SectionAwareRAGService":
        """Initialize from environment variables."""
        config_path = os.getenv("OCI_CONFIG_FILE", "~/.oci/config")
        config_profile = os.getenv("OCI_CONFIG_PROFILE", "DEFAULT")

        try:
            oci_config = oci.config.from_file(
                file_location=os.path.expanduser(config_path),
                profile_name=config_profile,
            )
        except Exception:
            logger.info("rag.using_instance_principal")
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            oci_config = {"signer": signer}

        return cls(
            oci_config=oci_config,
            agent_endpoint_id=os.environ["OCI_RAGA_AGENT_ENDPOINT_ID"],
            knowledge_base_id=os.environ["OCI_KNOWLEDGE_BASE_ID"],
            top_k=int(os.getenv("RAG_TOP_K", "5")),
            service_endpoint=os.getenv("OCI_AGENT_ENDPOINT"),
        )

    def retrieve_section_context(self, section: str, project_data: dict[str, Any]) -> list[SectionChunk]:
        """Retrieve top-k section-matched chunks with diagnostics and semantic fallback."""
        cache_key = self._cache_key(section, project_data)
        if cache_key in self._cache:
            return self._cache[cache_key]

        results = self._retrieve_by_section(section=section, project_data=project_data)
        self._cache[cache_key] = results
        return results

    def _retrieve_by_section(self, section: str, project_data: dict[str, Any]) -> list[SectionChunk]:
        """Retrieve chunks by calling OCI RAGA Search Documents API."""
        logger.info("rag.retrieve_start section=%s", section)

        try:
            doc_count = self.count()
            logger.info("rag.vector_store_total_docs count=%s", doc_count)
            if doc_count == 0:
                logger.error("rag.empty_vector_store - NO DOCUMENTS INDEXED")
                return []

            query = self._build_semantic_query(section, project_data)
            logger.info("rag.query section=%s query=%s", section, query[:100])

            response = self._search_documents(query=query, top_k=self.top_k * 2)
            documents = self._extract_documents(response)
            if not documents:
                logger.warning("rag.empty_response")
                return []

            all_results = list(documents)
            logger.info("rag.unfiltered_results count=%s", len(all_results))

            found_sections = {self._extract_section(doc) for doc in all_results}
            logger.info("rag.available_sections sections=%s", sorted(found_sections))

            filtered = [
                doc for doc in all_results if self._extract_section(doc).casefold() == section.casefold()
            ]
            logger.info("rag.filtered_results section=%s count=%s", section, len(filtered))

            selected = filtered if filtered else all_results[: self.top_k]
            chunks: list[SectionChunk] = []
            for doc in selected:
                services = self._extract_metadata(doc, "services", [])
                if isinstance(services, str):
                    services = [services]

                chunks.append(
                    SectionChunk(
                        section=self._extract_section(doc),
                        text=self._extract_text(doc),
                        client=str(self._extract_metadata(doc, "client", "") or ""),
                        industry=str(self._extract_metadata(doc, "industry", "") or ""),
                        services=tuple(str(item) for item in services if str(item).strip()),
                    )
                )

            return chunks[: self.top_k]
        except Exception:
            logger.exception("rag.retrieve_failed section=%s", section)
            return []

    def count(self) -> int:
        """Get document count from OCI Knowledge Base."""
        try:
            agent_client = GenerativeAiAgentClient(config=self._oci_config)
            kb_response = agent_client.get_knowledge_base(self.knowledge_base_id)
            document_count = getattr(kb_response.data, "document_count", None)
            if isinstance(document_count, int):
                return document_count
        except Exception:
            logger.exception("rag.count_knowledge_base_lookup_failed")

        try:
            test_response = self._search_documents(query="test", top_k=1)
            docs = self._extract_documents(test_response)
            return 1 if docs else 0
        except Exception:
            logger.exception("rag.count_failed")
            return 0

    def diagnose_vector_store(self) -> bool:
        """Diagnostic check using OCI API."""
        logger.info("rag.diagnostic_start")

        try:
            doc_count = self.count()
            logger.info("rag.diagnostic doc_count=%s", doc_count)

            if doc_count == 0:
                logger.error("rag.diagnostic_failed reason=empty_kb")
                return False

            test_results = self._retrieve_by_section("TEST QUERY", {})
            logger.info("rag.diagnostic test_results=%s", len(test_results))

            logger.info("rag.diagnostic_passed")
            return True
        except Exception:
            logger.exception("rag.diagnostic_exception")
            return False

    def refresh_from_env(self) -> int:
        """OCI KB auto-syncs from Object Storage; just return current count."""
        logger.info("rag.refresh_from_env - OCI KB auto-syncs")
        self._cache.clear()
        return self.count()


    def _search_documents(self, query: str, top_k: int) -> Any:
        """Invoke OCI search documents API (SDK method or raw REST fallback)."""
        if hasattr(self.runtime_client, "search_documents"):
            return self.runtime_client.search_documents(
                agent_endpoint_id=self.agent_endpoint_id,
                search_documents_details={"query": query, "top_k": top_k},
            )

        return self.runtime_client.base_client.call_api(
            resource_path="/agentEndpoints/{agentEndpointId}/actions/searchDocuments",
            method="POST",
            path_params={"agentEndpointId": self.agent_endpoint_id},
            body={"query": query, "top_k": top_k},
        )

    def _extract_documents(self, response: Any) -> list[Any]:
        """Extract documents list from OCI SDK or raw response payload."""
        data = getattr(response, "data", None)
        if hasattr(data, "documents") and getattr(data, "documents"):
            return list(data.documents)
        if isinstance(data, dict):
            docs = data.get("documents") or data.get("items") or []
            return list(docs) if isinstance(docs, list) else []
        if isinstance(response, tuple) and len(response) >= 1 and isinstance(response[0], dict):
            docs = response[0].get("documents") or []
            return list(docs) if isinstance(docs, list) else []
        return []

    def _extract_text(self, doc: Any) -> str:
        """Extract text content from OCI document object."""
        if isinstance(doc, dict):
            return str(doc.get("text") or doc.get("content") or doc)
        if hasattr(doc, "text") and getattr(doc, "text"):
            return str(doc.text)
        if hasattr(doc, "content") and getattr(doc, "content"):
            return str(doc.content)
        return str(doc)

    def _extract_section(self, doc: Any) -> str:
        """Extract section from document metadata."""
        value = self._extract_metadata(doc, "section", "UNKNOWN")
        return str(value or "UNKNOWN")

    def _extract_metadata(self, doc: Any, key: str, default: Any = None) -> Any:
        """Extract specific metadata field."""
        if isinstance(doc, dict):
            metadata = doc.get("metadata")
            if isinstance(metadata, dict):
                return metadata.get(key, default)
            return default

        metadata = getattr(doc, "metadata", None)
        if isinstance(metadata, dict):
            return metadata.get(key, default)
        if metadata and hasattr(metadata, key):
            return getattr(metadata, key)
        return default

    def _build_semantic_query(self, section: str, project_data: dict[str, Any]) -> str:
        """Build rich query for better retrieval."""
        parts = [section]

        if project_data.get("client"):
            parts.append(f"client: {project_data['client']}")

        if project_data.get("industry"):
            parts.append(f"industry: {project_data['industry']}")

        if project_data.get("services"):
            services = ", ".join(str(s) for s in project_data["services"])
            parts.append(f"services: {services}")

        return " ".join(parts)

    def _cache_key(self, section: str, project_data: dict[str, Any]) -> str:
        raw = json.dumps(project_data, sort_keys=True, ensure_ascii=False)
        return f"{section.casefold()}::{raw}"
