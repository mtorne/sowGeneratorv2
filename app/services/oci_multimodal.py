"""OCI multimodal client wrapper for architecture diagram analysis."""

from __future__ import annotations

import logging

import oci
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference.models import (
    BaseChatRequest,
    ChatDetails,
    GenericChatRequest,
    ImageContent,
    ImageUrl,
    Message,
    OnDemandServingMode,
    TextContent,
)

from app.config.settings import OCISettings
from app.services.llm import _extract_text

logger = logging.getLogger(__name__)


class OCIClient:
    """Thin adapter exposing multimodal_completion for VisionAgent DI."""

    def __init__(self, settings: OCISettings | None = None) -> None:
        self.settings = settings or OCISettings.from_env()
        self._client = self._build_client()

    def _build_client(self) -> GenerativeAiInferenceClient:
        oci_config = oci.config.from_file(file_location=self.settings.config_file, profile_name=self.settings.profile)
        return GenerativeAiInferenceClient(
            config=oci_config,
            service_endpoint=self.settings.endpoint,
            timeout=(self.settings.timeout_connect, self.settings.timeout_read),
            retry_strategy=oci.retry.NoneRetryStrategy(),
        )

    def multimodal_completion(self, prompt: str, image_base64: str, mime_type: str, **kwargs: object) -> str:
        model_name = str(kwargs.get("model_name") or self.settings.multimodal_model_name)

        image_content = ImageContent(
            type="IMAGE",
            image_url=ImageUrl(url=f"data:{mime_type};base64,{image_base64}"),
        )
        text_content = TextContent(type="TEXT", text=prompt)
        message = Message(role="USER", content=[text_content, image_content])

        request = GenericChatRequest(
            messages=[message],
            api_format=BaseChatRequest.API_FORMAT_GENERIC,
            temperature=0,
            top_p=0.9,
            top_k=-1,
            max_tokens=4000,
        )
        details = ChatDetails(
            compartment_id=self.settings.compartment_id,
            serving_mode=OnDemandServingMode(model_id=model_name),
            chat_request=request,
        )

        try:
            response = self._client.chat(details)
            return _extract_text(response)
        except oci.exceptions.ServiceError as exc:
            logger.exception("oci_multimodal.service_error")
            raise RuntimeError(f"OCI multimodal service error: {exc.message}") from exc
        except Exception as exc:
            logger.exception("oci_multimodal.unexpected_error")
            raise RuntimeError("Unexpected OCI multimodal invocation failure") from exc
