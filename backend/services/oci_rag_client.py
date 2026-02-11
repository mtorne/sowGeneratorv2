import json
import logging
import time
from typing import Any, Dict, Optional

from oci import config, retry
from oci.generative_ai_agent_runtime import GenerativeAiAgentRuntimeClient
from oci.generative_ai_agent_runtime.models import CreateSessionDetails, ChatDetails

from config.settings import oci_config

logger = logging.getLogger(__name__)


class OCIRAGService:
    """Service wrapper for OCI Generative AI Agent Runtime (RAG chat endpoint)."""

    def __init__(self):
        self.config = config.from_file()
        self.client = GenerativeAiAgentRuntimeClient(
            config=self.config,
            service_endpoint=oci_config.agent_endpoint,
            retry_strategy=retry.NoneRetryStrategy(),
            timeout=(oci_config.timeout_connect, oci_config.timeout_read),
        )
        self.agent_endpoint_id = oci_config.agent_endpoint_id
        self._cached_session_id: Optional[str] = None

    def chat(self, message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a chat message to an OCI Agent endpoint and return normalized output."""
        if not self.agent_endpoint_id:
            raise ValueError("OCI agent endpoint id is not configured")

        resolved_session_id = session_id or self._cached_session_id
        if resolved_session_id == "new":
            resolved_session_id = None

        # Create session only when absolutely required. This is critical for avoiding
        # OCI Agent Endpoint session-limit (429) errors under load.
        if not resolved_session_id:
            logger.info("No reusable session_id provided. Creating OCI GenAI Session...")
            create_session_details = CreateSessionDetails(
                display_name="UserSession",
                description="Chat session initialized via RAG API",
            )
            last_error: Optional[Exception] = None
            for attempt in range(1, 4):
                try:
                    session_response = self.client.create_session(
                        create_session_details=create_session_details,
                        agent_endpoint_id=self.agent_endpoint_id,
                    )
                    resolved_session_id = session_response.data.id
                    self._cached_session_id = resolved_session_id
                    logger.info("Created new session: %s", resolved_session_id)
                    break
                except Exception as exc:
                    last_error = exc
                    message_text = str(exc).lower()
                    if "session per agent endpoint limit is exceeded" in message_text and attempt < 3:
                        delay = 0.2 * attempt
                        logger.warning(
                            "OCI session limit hit while creating session (attempt %s/3). Retrying in %.1fs",
                            attempt,
                            delay,
                        )
                        time.sleep(delay)
                        continue
                    logger.error("Failed to create session: %s", str(exc))
                    break

            if not resolved_session_id:
                # Try cached session as a fallback if one exists.
                if self._cached_session_id:
                    logger.warning("Using cached session fallback after create_session failure.")
                    resolved_session_id = self._cached_session_id
                else:
                    raise RuntimeError(f"Failed to create OCI GenAI session: {last_error}")

        chat_details = ChatDetails(
            user_message=message,
            should_stream=False,
            session_id=resolved_session_id,
        )

        response = self.client.chat(
            agent_endpoint_id=self.agent_endpoint_id,
            chat_details=chat_details,
        )

        data = response.data
        content = None
        if getattr(data, "message", None) and getattr(data.message, "content", None):
            content = getattr(data.message.content, "text", None)

        session_from_headers = (
            response.headers.get("session-id")
            or response.headers.get("opc-agent-session-id")
            or response.headers.get("x-session-id")
        )
        if session_from_headers:
            self._cached_session_id = session_from_headers

        citations = []
        if getattr(data, "message", None) and getattr(data.message, "content", None):
            citations = getattr(data.message.content, "citations", None) or []

        logger.info("RAG chat completed. Session: %s", session_from_headers or resolved_session_id)

        return {
            "answer": content or "",
            "session_id": session_from_headers or resolved_session_id,
            "citations": [self._serialize_citation(c) for c in citations],
            "guardrail_result": getattr(data, "guardrail_result", None),
        }

    @staticmethod
    def _serialize_citation(citation: Any) -> Dict[str, Any]:
        source = getattr(citation, "source", citation)
        if isinstance(source, dict):
            return source

        if hasattr(source, "to_dict"):
            try:
                return source.to_dict()
            except Exception:
                pass

        source_text = str(source)
        try:
            parsed = json.loads(source_text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        return {"source_uri": source_text}
