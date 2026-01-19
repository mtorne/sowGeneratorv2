from oci import config, retry
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference.models import (
    ImageUrl, ImageContent, TextContent, Message,
    ChatDetails, GenericChatRequest, BaseChatRequest,
    OnDemandServingMode, CohereChatRequest
)
from typing import List, Optional
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
            timeout=(oci_config.timeout_connect, oci_config.timeout_read)
        )
        # Default models (should be in your settings.py, but defaults here for safety)
        self.model_llama_text = getattr(oci_config, 'model_id_llama', "meta.llama-3.1-70b-instruct")
        self.model_llama_vision = getattr(oci_config, 'model_id_vision', "meta.llama-3.2-90b-vision-instruct")
        self.model_cohere = getattr(oci_config, 'model_id_cohere', "cohere.command-r-plus")

    def _build_generic_request(self, messages: List[Message], max_tokens=2000,top_k_override=None) -> GenericChatRequest:
        """Builds a Generic request (for Llama models)"""

         # Determine Top K
        if top_k_override is not None:
             k_val = top_k_override
        else:
             # Default from config (likely -1 for Llama)
             k_val = int(getattr(oci_config, 'top_k', -1))

        return GenericChatRequest(
            messages=messages,
            max_tokens=max_tokens,
            temperature=oci_config.temperature,
            top_p=oci_config.top_p,
            top_k=k_val    ,
            api_format=BaseChatRequest.API_FORMAT_GENERIC
        )
    
    def _build_cohere_request(self, prompt: str, max_tokens=2000) -> CohereChatRequest:
        """Builds a request specifically for Cohere models"""
        return CohereChatRequest(
            message=prompt,
            max_tokens=max_tokens,
            temperature=oci_config.temperature,
            frequency_penalty=0.0,
            top_p=oci_config.top_p,
            top_k=int(oci_config.top_k), # Ensure int
            api_format=BaseChatRequest.API_FORMAT_COHERE
        )
    
    def _call_model(self, model_id: str, chat_request: BaseChatRequest) -> str:
        """Helper to execute the OCI API call"""
        try:
            chat_detail = ChatDetails(
                compartment_id=oci_config.compartment_id,
                serving_mode=OnDemandServingMode(model_id=model_id),
                chat_request=chat_request
            )
            response = self.client.chat(chat_detail)
            
            # Check response structure (Llama vs Cohere can differ slightly in wrapper)
            if hasattr(response.data, 'chat_response'):
                # Generic model response (Llama)
                if hasattr(response.data.chat_response, 'choices'):
                    return response.data.chat_response.choices[0].message.content[0].text
                # Cohere model response
                elif hasattr(response.data.chat_response, 'text'):
                    return response.data.chat_response.text
            
            return str(response.data)

        except Exception as e:
            logger.error(f"OCI GenAI Call Error (Model: {model_id}): {str(e)}")
            raise e

    def analyze_diagram(self, image_data_uri: str, prompt: str, model_id: str = None) -> str:
        """
        Analyze a diagram using Llama 3.2 Vision (Multimodal)
        """
        target_model = model_id if model_id else self.model_llama_vision
        logger.info(f"Analyzing diagram... Model: {target_model}")
        image_content = ImageContent(image_url=ImageUrl(url=image_data_uri))
        text_content = TextContent(text=prompt)
        message = Message(role="USER", content=[image_content, text_content])

         # Determine safe top_k
        safe_top_k = -1 # Default for Llama
        if "gemini" in target_model.lower():
             safe_top_k = 40 # Standard for Gemini
        elif "grok" in target_model.lower():
             safe_top_k = 40 # Safe bet for Grok/OpenAI-like
        
        request = self._build_generic_request([message], max_tokens=1500, top_k_override=safe_top_k)
        return self._call_model(target_model, request)
        #return self._call_model(self.model_llama_vision, request)

    
    def generate_text_content(self, prompt: str, provider: str = "llama", model_id: str = None) -> str:
        """
        Generate text content using the specified provider model.
        
        Args:
            prompt: Text prompt
            provider: 'llama', 'cohere', or 'generic'
            model_id: Specific model OCID/Name
        """
        # 1. Determine the Target Model
        target_model = model_id
        if not target_model:
            target_model = self.model_cohere if "cohere" in provider.lower() else self.model_llama_text
            
        logger.info(f"Generating text... Provider: {provider.upper()} | Model: {target_model}")
        
        # 2. Handle Cohere (Special Request Format)
        if "cohere" in provider.lower() or "cohere" in target_model.lower():
            request = self._build_cohere_request(prompt)
            return self._call_model(target_model, request)
            
        # 3. Handle Generic (Llama, Gemini, Grok, OpenAI)
        else: 
            # --- CRITICAL FIX: Adjust Top_K based on Model Type ---
            # Llama loves -1. Gemini/Grok hate it and need a positive integer (e.g. 40).
            current_top_k = -1  # Default for Llama
            
            if "gemini" in target_model.lower():
                current_top_k = 40  # Valid for Gemini
            elif "grok" in target_model.lower():
                current_top_k = 40  # Valid for Grok
            elif "openai" in target_model.lower():
                 current_top_k = 1  # Often safer for OpenAI OSS wrappers
                 
            # Create Message
            message = Message(role="USER", content=[TextContent(text=prompt)])
            
            # Build Request with Override
            request = self._build_generic_request(
                [message], 
                top_k_override=current_top_k
            )
            
            # --- CRITICAL FIX: Use target_model, NOT self.model_llama_text ---
            return self._call_model(target_model, request)
