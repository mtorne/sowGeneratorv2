"""OCI Generative AI wrapper service."""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import oci
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference.models import (
    BaseChatRequest,
    ChatDetails,
    GenericChatRequest,
    Message,
    OnDemandServingMode,
    TextContent,
)

from app.config.settings import OCISettings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# HTTP status codes that indicate a transient OCI server-side error worth retrying.
_RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
_MAX_LLM_RETRIES = 3


def _call_with_retry(
    fn: Callable[..., _T],
    *args: Any,
    max_retries: int = _MAX_LLM_RETRIES,
    **kwargs: Any,
) -> _T:
    """Call *fn* with exponential backoff on transient OCI ServiceErrors.

    Retries up to *max_retries* times on HTTP 429/5xx responses.
    Other exceptions (parsing failures, 4xx client errors) propagate immediately.
    """
    for attempt in range(1, max_retries + 2):  # +2: initial attempt + max_retries
        try:
            return fn(*args, **kwargs)
        except oci.exceptions.ServiceError as exc:
            if exc.status not in _RETRYABLE_HTTP_STATUSES or attempt > max_retries:
                raise
            wait = min(1.0 * (2 ** (attempt - 1)) + random.uniform(0, 1.0), 30.0)
            logger.warning(
                "OCI transient error status=%s attempt=%d/%d retrying in %.1fs: %s",
                exc.status,
                attempt,
                max_retries,
                wait,
                exc.message,
            )
            time.sleep(wait)
    # Unreachable — loop always returns or raises.
    raise RuntimeError("_call_with_retry exhausted without result")  # pragma: no cover


@dataclass(frozen=True)
class LLMConfig:
    """Configuration values for OCI Generative AI client."""

    config_file: str
    profile: str
    endpoint: str
    model_id: str
    compartment_id: str
    temperature: float
    timeout_connect: float
    timeout_read: float
    max_tokens: int

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Build config from environment variables."""
        oci_settings = OCISettings.from_env()
        return cls(
            config_file=oci_settings.config_file,
            profile=oci_settings.profile,
            endpoint=oci_settings.endpoint,
            model_id=oci_settings.model_id,
            compartment_id=oci_settings.compartment_id,
            temperature=oci_settings.temperature,
            timeout_connect=oci_settings.timeout_connect,
            timeout_read=oci_settings.timeout_read,
            max_tokens=int(os.getenv("OCI_MAX_TOKENS", "6000")),
        )


def _build_client(config: LLMConfig) -> GenerativeAiInferenceClient:
    """Instantiate OCI Generative AI client."""
    oci_config = oci.config.from_file(file_location=config.config_file, profile_name=config.profile)
    return GenerativeAiInferenceClient(
        config=oci_config,
        service_endpoint=config.endpoint,
        timeout=(config.timeout_connect, config.timeout_read),
        retry_strategy=oci.retry.NoneRetryStrategy(),
    )


def _extract_text(response: object) -> str:
    """Extract message text from OCI response object."""
    try:
        chat_response = response.data.chat_response
        choices = chat_response.choices
        if not choices:
            raise ValueError("OCI response does not contain choices")
        content_parts = choices[0].message.content
        text_parts = [part.text for part in content_parts if hasattr(part, "text") and part.text]
        result = "\n".join(text_parts).strip()
        if not result:
            raise ValueError("OCI response text is empty")
        return result
    except Exception as exc:
        logger.exception("Failed to parse OCI response")
        raise RuntimeError("Unable to parse LLM response") from exc


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Send a prompt to OCI Generative AI and return plain text response."""
    mock_response = os.getenv("MOCK_LLM_RESPONSE")
    if mock_response is not None:
        logger.info("Using MOCK_LLM_RESPONSE for local testing")
        return mock_response

    config = LLMConfig.from_env()
    client = _build_client(config)

    combined_prompt = f"System:\n{system_prompt.strip()}\n\nUser:\n{user_prompt.strip()}"
    message = Message(role="USER", content=[TextContent(text=combined_prompt)])
    chat_request = GenericChatRequest(
        messages=[message],
        api_format=BaseChatRequest.API_FORMAT_GENERIC,
        temperature=config.temperature,
        top_p=0.9,
        # OCI rejects top_k < 1 for some models (e.g., Gemini via OCI wrapper).
        top_k=1,
        max_tokens=config.max_tokens,
    )
    details = ChatDetails(
        compartment_id=config.compartment_id,
        serving_mode=OnDemandServingMode(model_id=config.model_id),
        chat_request=chat_request,
    )

    try:
        logger.info("Calling OCI Generative AI model")
        response = _call_with_retry(client.chat, details)
        return _extract_text(response)
    except oci.exceptions.ServiceError as exc:
        logger.exception("OCI service error status=%s", exc.status)
        raise RuntimeError(f"OCI service error: {exc.message}") from exc
    except Exception as exc:
        logger.exception("Unexpected OCI LLM error")
        raise RuntimeError("Unexpected LLM invocation failure") from exc
