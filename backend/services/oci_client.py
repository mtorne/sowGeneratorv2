from oci import config, retry
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference import models as oci_models
from typing import Any, Dict, List, Optional

BaseChatRequest = oci_models.BaseChatRequest
ChatDetails = oci_models.ChatDetails
CohereChatRequest = oci_models.CohereChatRequest
GenericChatRequest = oci_models.GenericChatRequest
ImageContent = oci_models.ImageContent
ImageUrl = oci_models.ImageUrl
Message = oci_models.Message
OnDemandServingMode = oci_models.OnDemandServingMode
TextContent = oci_models.TextContent
JsonSchemaResponseFormat = getattr(oci_models, "JsonSchemaResponseFormat", None)
ResponseJsonSchema = getattr(oci_models, "ResponseJsonSchema", None)
import logging
from config.settings import oci_config

logger = logging.getLogger(__name__)


class OCIGenAIService:
    """
    Service class for interacting with OCI Generative AI Service.
    Supports Llama 3 (Text & Vision) and Cohere Command R+.
    """

    def __init__(self):
        self.config = config.from_file()
        self.client = GenerativeAiInferenceClient(
            config=self.config,
            service_endpoint=oci_config.endpoint,
            retry_strategy=retry.NoneRetryStrategy(),
            timeout=(oci_config.timeout_connect, oci_config.timeout_read),
        )
        self.model_llama_text = getattr(oci_config, "model_id_llama", "meta.llama-3.1-70b-instruct")
        self.model_llama_vision = getattr(oci_config, "model_id_vision", "meta.llama-3.2-90b-vision-instruct")
        self.model_cohere = getattr(oci_config, "model_id_cohere", "cohere.command-r-plus")

    def _build_response_format(self, response_format: Optional[Dict[str, Any]]) -> Optional[Any]:
        """Build OCI SDK response_format object from dict payload.

        Expected input shape:
        {
          "type": "JSON_SCHEMA",
          "json_schema": {
            "name": "...",
            "strict": true,
            "schema": {...}
          }
        }
        """
        if not response_format:
            return None
        if str(response_format.get("type", "")).upper() != "JSON_SCHEMA":
            return None
        if JsonSchemaResponseFormat is None or ResponseJsonSchema is None:
            logger.info("OCI SDK does not support JSON schema response_format; continuing without it.")
            return None

        schema_cfg = response_format.get("json_schema") or {}
        try:
            json_schema = ResponseJsonSchema(
                name=schema_cfg.get("name") or "structured_output",
                description=schema_cfg.get("description"),
                schema=schema_cfg.get("schema") or {},
                is_strict=bool(schema_cfg.get("strict", False)),
            )
            return JsonSchemaResponseFormat(type=JsonSchemaResponseFormat.TYPE_JSON_SCHEMA, json_schema=json_schema)
        except Exception as exc:
            logger.warning("Failed to build JSON_SCHEMA response format object, continuing without it: %s", exc)
            return None

    def _build_generic_request(
        self,
        messages: List[Message],
        max_tokens=2000,
        top_k_override=None,
        response_format: Optional[Dict[str, Any]] = None,
        temperature_override: Optional[float] = None,
    ) -> GenericChatRequest:
        """Build a Generic request (for Llama/Grok/Gemini wrappers)."""
        if top_k_override is not None:
            k_val = top_k_override
        else:
            k_val = int(getattr(oci_config, "top_k", -1))

        request = GenericChatRequest(
            messages=messages,
            max_tokens=max_tokens,
            temperature=float(oci_config.temperature if temperature_override is None else temperature_override),
            top_p=oci_config.top_p,
            top_k=k_val,
            api_format=BaseChatRequest.API_FORMAT_GENERIC,
        )

        schema_response = self._build_response_format(response_format)
        if schema_response:
            request.response_format = schema_response

        return request

    def _resolve_max_tokens(self, model_id: str) -> int:
        model = (model_id or "").lower()
        if "gemini" in model:
            return int(getattr(oci_config, "gemini_max_output_tokens", 4096))
        return max(3000, int(getattr(oci_config, "max_output_tokens", 4000)))

    def _build_cohere_request(self, prompt: str, max_tokens=2000) -> CohereChatRequest:
        return CohereChatRequest(
            message=prompt,
            max_tokens=max_tokens,
            temperature=oci_config.temperature,
            frequency_penalty=0.0,
            top_p=oci_config.top_p,
            top_k=int(oci_config.top_k),
            api_format=BaseChatRequest.API_FORMAT_COHERE,
        )

    def _call_model(self, model_id: str, chat_request: BaseChatRequest) -> str:
        """Helper to execute the OCI API call."""
        try:
            chat_detail = ChatDetails(
                compartment_id=oci_config.compartment_id,
                serving_mode=OnDemandServingMode(model_id=model_id),
                chat_request=chat_request,
            )
            response = self.client.chat(chat_detail)

            if hasattr(response.data, "chat_response"):
                if hasattr(response.data.chat_response, "choices"):
                    first_choice = response.data.chat_response.choices[0]
                    finish_reason = getattr(first_choice, "finish_reason", None)
                    if finish_reason:
                        logger.info(f"Model finish reason ({model_id}): {finish_reason}")

                    content_parts = first_choice.message.content
                    text_parts = [part.text for part in content_parts if hasattr(part, "text") and part.text]
                    if text_parts:
                        text_response = "\n".join(text_parts)
                        if str(finish_reason).lower() in {"length", "max_tokens", "token_limit"}:
                            logger.warning(f"Response may be truncated for model {model_id}: finish_reason={finish_reason}")
                        return text_response
                    return str(content_parts)
                if hasattr(response.data.chat_response, "text"):
                    return response.data.chat_response.text

            return str(response.data)

        except Exception as e:
            logger.error(f"OCI GenAI Call Error (Model: {model_id}): {str(e)}")
            raise e

    def _is_request_format_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return "please pass in correct format of request" in text or "status': 400" in text or '"status": 400' in text

    def analyze_diagram(
        self,
        image_data_uri: str,
        prompt: str,
        model_id: str = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> str:
        target_model = model_id if model_id else self.model_llama_vision
        logger.info(f"Analyzing diagram... Model: {target_model}")
        image_content = ImageContent(image_url=ImageUrl(url=image_data_uri))
        text_content = TextContent(text=prompt)
        message = Message(role="USER", content=[image_content, text_content])

        safe_top_k = -1
        if "gemini" in target_model.lower() or "grok" in target_model.lower():
            safe_top_k = 40

        request = self._build_generic_request(
            [message],
            max_tokens=self._resolve_max_tokens(target_model),
            top_k_override=safe_top_k,
            response_format=response_format,
            temperature_override=0,
        )

        try:
            return self._call_model(target_model, request)
        except Exception as exc:
            if response_format and self._is_request_format_error(exc):
                logger.warning("Retrying diagram analysis without response_format due to 400 request format validation.")
                fallback_request = self._build_generic_request(
                    [message],
                    max_tokens=self._resolve_max_tokens(target_model),
                    top_k_override=safe_top_k,
                    response_format=None,
                    temperature_override=0,
                )
                return self._call_model(target_model, fallback_request)
            raise

    def generate_text_content(
        self,
        prompt: str,
        provider: str = "llama",
        model_id: str = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate text content using selected provider/model."""
        target_model = model_id
        if not target_model:
            target_model = self.model_cohere if "cohere" in provider.lower() else self.model_llama_text

        logger.info(f"Generating text... Provider: {provider.upper()} | Model: {target_model}")

        if "cohere" in provider.lower() or "cohere" in target_model.lower():
            request = self._build_cohere_request(prompt)
            return self._call_model(target_model, request)

        current_top_k = -1
        if "gemini" in target_model.lower() or "grok" in target_model.lower():
            current_top_k = 40
        elif "openai" in target_model.lower():
            current_top_k = 1

        message = Message(role="USER", content=[TextContent(text=prompt)])
        request = self._build_generic_request(
            [message],
            max_tokens=self._resolve_max_tokens(target_model),
            top_k_override=current_top_k,
            response_format=response_format,
        )

        try:
            return self._call_model(target_model, request)
        except Exception as exc:
            # Fallback for models/endpoints that reject responseFormat payloads.
            if response_format and self._is_request_format_error(exc):
                logger.warning("Retrying OCI chat without response_format due to 400 request format validation.")
                fallback_request = self._build_generic_request(
                    [message],
                    max_tokens=self._resolve_max_tokens(target_model),
                    top_k_override=current_top_k,
                    response_format=None,
                )
                return self._call_model(target_model, fallback_request)
            raise
