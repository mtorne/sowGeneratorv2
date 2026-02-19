"""Section-aware retrieval service for SoW generation."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
import re  # add at top of file if not already there
import oci
from oci import retry
from oci.generative_ai_agent import GenerativeAiAgentClient
from oci.generative_ai_agent_runtime import GenerativeAiAgentRuntimeClient

from app.config.settings import OCISettings


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
        self.session_id: str | None = None
        self._cache: dict[str, list[SectionChunk]] = {}

        if runtime_client is not None:
            self.runtime_client = runtime_client
        else:
            self.runtime_client = GenerativeAiAgentRuntimeClient(
                config=self._oci_config,
                service_endpoint=service_endpoint,
                retry_strategy=retry.NoneRetryStrategy(),
                timeout=(
                    int(oci_config.get("timeout_connect", 10)) if isinstance(oci_config, dict) else 10,
                    int(oci_config.get("timeout_read", 120)) if isinstance(oci_config, dict) else 120,
                ),
            )

        logger.info(
            "rag.initialized agent_endpoint=%s kb=%s",
            agent_endpoint_id[-20:],
            knowledge_base_id[-20:],
        )

    @classmethod
    def from_env(cls) -> "SectionAwareRAGService":
        """Initialize from OCI settings and env-backed credentials."""
        settings = OCISettings.from_env()

        try:
            oci_config = oci.config.from_file(
                file_location=settings.config_file,
                profile_name=settings.profile,
            )
        except Exception:
            logger.info("rag.using_instance_principal")
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            oci_config = {"signer": signer}

        oci_config["timeout_connect"] = settings.timeout_connect
        oci_config["timeout_read"] = settings.timeout_read

        return cls(
            oci_config=oci_config,
            agent_endpoint_id=settings.agent_endpoint_id,
            knowledge_base_id=settings.knowledge_base_id,
            top_k=settings.rag_top_k,
            service_endpoint=settings.agent_endpoint,
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
        """Retrieve chunks by calling OCI Agent Chat API."""
        logger.info("rag.retrieve_start section=%s", section)

        try:
            doc_count = self.count()
            logger.info("rag.vector_store_total_docs count=%s", doc_count)
            if doc_count == 0:
                logger.error("rag.empty_vector_store - NO DOCUMENTS INDEXED")
                return []

            query = self._build_semantic_query(section, project_data)
            logger.info("rag.query section=%s query=%s", section, query[:100])

            response = self._search_via_chat(query=query, top_k=self.top_k * 2)
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
            test_response = self._search_via_chat(query="test", top_k=1)
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


    def _create_session(self) -> str:
        """Create agent session for chat-based retrieval."""
        from oci.generative_ai_agent_runtime.models import CreateSessionDetails

        session_details = CreateSessionDetails(display_name=f"sow-rag-{int(time.time())}")

        response = self.runtime_client.create_session(
            agent_endpoint_id=self.agent_endpoint_id,
            create_session_details=session_details,
        )

        self.session_id = response.data.id
        logger.info("rag.session_created session_id=%s", self.session_id)
        return self.session_id

    def _search_via_chat(self, query: str, top_k: int) -> Any:
        """Search using Chat API (required for tool-based RAG)."""
        from oci.generative_ai_agent_runtime.models import ChatDetails

        if not self.session_id:
            self._create_session()

        chat_details = ChatDetails(
            user_message=f"Retrieve {top_k} relevant documents about: {query}",
            should_stream=False,
            session_id=self.session_id,
        )

        return self.runtime_client.chat(
            agent_endpoint_id=self.agent_endpoint_id,
            chat_details=chat_details,
        )

    def _extract_documents(self, response: Any) -> list[Any]:
        """Extract documents from Chat API response (includes citations)."""
        data = getattr(response, "data", None)

        if not data or not hasattr(data, "message"):
            return []

        message = data.message
        content = getattr(message, "content", None)

        if content and hasattr(content, "citations") and content.citations:
            return list(content.citations)

        if hasattr(message, "citations") and message.citations:
            return list(message.citations)

        if content:
            text = getattr(content, "text", None)
            if text:
                return [{"text": text, "metadata": {}}]
            return [{"text": str(content), "metadata": {}}]

        if hasattr(message, "text") and message.text:
            return [{"text": str(message.text), "metadata": {}}]

        return []

    def _extract_text(self, doc: Any) -> str:
        """Extract text from citation/document object."""
        if hasattr(doc, "source_text"):
            return str(doc.source_text)

        if isinstance(doc, dict):
            return str(doc.get("text") or doc.get("content") or doc.get("source_text") or doc)

        if hasattr(doc, "text"):
            return str(doc.text)

        if hasattr(doc, "content"):
            content = doc.content
            if hasattr(content, "text") and content.text:
                return str(content.text)
            return str(content)

        return str(doc)

 

    def _extract_section(self, doc: Any) -> str:
        """Extract section from metadata or frontmatter embedded in chunk text."""
        # First try structured metadata (opc_meta / source_location)
        value = self._extract_metadata(doc, "section", None)
        if value and str(value).strip().upper() not in ("", "UNKNOWN"):
            return str(value).strip().upper()

        # Fall back to parsing YAML frontmatter from chunk body
        text = self._extract_text(doc)
        match = re.match(r"^---\s*\nsection:\s*(.+?)\s*\n", text, re.IGNORECASE)
        if match:
            return match.group(1).strip().upper()

        return "UNKNOWN"


    def _extract_metadata(self, doc: Any, key: str, default: Any = None) -> Any:
        """Extract metadata from citation/document."""
        if hasattr(doc, "source_location"):
            source_loc = doc.source_location
            if hasattr(source_loc, "metadata") and isinstance(source_loc.metadata, dict):
                return source_loc.metadata.get(key, default)

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

    SECTION_QUERY_MAP = {
    "SOW VERSION HISTORY":               "document version history revision changes amendments",
    "STATUS AND NEXT STEPS":             "project status milestones next steps actions timeline",
    "PROJECT PARTICIPANTS":              "project team roles responsibilities stakeholders contacts",
    "IN SCOPE APPLICATION":             "applications systems in scope workloads included services",
    "PROJECT OVERVIEW":                  "project objectives goals background executive summary",
    "CURRENT STATE ARCHITECTURE":        "current architecture existing infrastructure on-premise legacy systems",
    "CURRENTLY USED TECHNOLOGY STACK":   "current technology stack software tools databases middleware",
    "OCI SERVICE SIZING AND AMOUNTS":    "OCI cloud service sizing compute storage license quantities",
    "FUTURE STATE ARCHITECTURE":         "target architecture future state cloud migration OCI design",
    "ARCHITECTURE DEPLOYMENT OVERVIEW":  "deployment architecture network topology zones regions availability",
    "CLOSING FEEDBACK":                  "closing remarks acceptance criteria success metrics feedback",
}

    def _build_semantic_query(self, section: str, project_data: dict[str, Any]) -> str:
        """Build rich semantic query using section-specific descriptors."""
        base = self.SECTION_QUERY_MAP.get(section.upper().strip(), section)
        parts = [base]

        if project_data.get("client"):
            parts.append(project_data["client"])
        if project_data.get("industry"):
            parts.append(project_data["industry"])
        if project_data.get("services"):
            parts.append(", ".join(str(s) for s in project_data["services"]))

        return " ".join(parts)

    def _cache_key(self, section: str, project_data: dict[str, Any]) -> str:
        raw = json.dumps(project_data, sort_keys=True, ensure_ascii=False)
        return f"{section.casefold()}::{raw}"
