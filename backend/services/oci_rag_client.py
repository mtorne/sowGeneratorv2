import logging
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

    def chat(self, message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a chat message to an OCI Agent endpoint and return normalized output."""
        if not self.agent_endpoint_id:
            raise ValueError("OCI agent endpoint id is not configured")
        
         # --- FIX STARTS HERE ---
         # 1. Create a session if we don't have one (first request)
        if not session_id or session_id == 'new':
            logger.info("No session_id provided. Creating new OCI GenAI Session...")
            create_session_details = CreateSessionDetails(
                display_name="UserSession",
                description="Chat session initialized via RAG API"
            )
            try:
                session_response = self.client.create_session(
                    create_session_details=create_session_details,
                    agent_endpoint_id=self.agent_endpoint_id
                )
                session_id = session_response.data.id
                logger.info("Created new session: %s", session_id)
            except Exception as e:
                logger.error("Failed to create session: %s", str(e))
                raise
        # --- FIX ENDS HERE ---


        chat_details = ChatDetails(
            user_message=message,
            should_stream=False,
            session_id=session_id,
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

        citations = []
        if getattr(data, "message", None) and getattr(data.message, "content", None):
            citations = getattr(data.message.content, "citations", None) or []

        logger.info("RAG chat completed. Session: %s", session_from_headers or session_id)

        return {
            "answer": content or "",
            "session_id": session_from_headers or session_id,
            "citations": [getattr(c, "source", str(c)) for c in citations],
            "guardrail_result": getattr(data, "guardrail_result", None),
        }
