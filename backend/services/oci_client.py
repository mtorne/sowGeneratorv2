from oci import config, retry
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference.models import (
    ImageUrl, ImageContent, TextContent, Message,
    ChatDetails, GenericChatRequest, BaseChatRequest,
    OnDemandServingMode
)
from typing import List
import logging
from config.settings import oci_config

logger = logging.getLogger(__name__)

class OCIGrokService:
    """Service class for interacting with OCI Grok API"""
    
    def __init__(self):
        self.config = config.from_file()
        self.client = GenerativeAiInferenceClient(
            config=self.config,
            service_endpoint=oci_config.endpoint,
            retry_strategy=retry.NoneRetryStrategy(),
            timeout=(oci_config.timeout_connect, oci_config.timeout_read)
        )
    
    def build_chat_request_llama(self, messages: List[Message]) -> GenericChatRequest:
        """Build a chat request with configured parameters"""
        return GenericChatRequest(
            messages=messages,
            max_tokens=oci_config.max_tokens_llama,
            temperature=oci_config.temperature,
            top_p=oci_config.top_p,
            top_k=oci_config.top_k_llama,
            api_format=BaseChatRequest.API_FORMAT_GENERIC
        )
    
    def build_chat_request(self, messages: List[Message]) -> GenericChatRequest:
        """Build a chat request with configured parameters"""
        return GenericChatRequest(
            messages=messages,
            max_tokens=oci_config.max_tokens,
            temperature=oci_config.temperature,
            top_p=oci_config.top_p,
            top_k=oci_config.top_k,
            api_format=BaseChatRequest.API_FORMAT_GENERIC
        )
    
    def call_llama(self, messages: List[Message]) -> str:
        """
        Call Grok API with messages and return the response text
        
        Args:
            messages: List of Message objects
            
        Returns:
            Response text from Grok
            
        Raises:
            Exception: If API call fails
        """
        try:
            chat_detail = ChatDetails(
                compartment_id=oci_config.compartment_id,
                serving_mode=OnDemandServingMode(model_id=oci_config.model_id_llama),
                chat_request=self.build_chat_request_llama(messages)
            )
            response = self.client.chat(chat_detail)
            logger.info(f"oci service call response - completion tokens:  {response.data.chat_response.usage.completion_tokens}")
            return response.data.chat_response.choices[0].message.content[0].text
        except Exception as e:
            logger.error(f"Error calling Grok API: {str(e)}")
            raise
    
    def call_grok(self, messages: List[Message]) -> str:
        """
        Call Grok API with messages and return the response text
        
        Args:
            messages: List of Message objects
            
        Returns:
            Response text from Grok
            
        Raises:
            Exception: If API call fails
        """
        try:
            chat_detail = ChatDetails(
                compartment_id=oci_config.compartment_id,
                serving_mode=OnDemandServingMode(model_id=oci_config.model_id),
                chat_request=self.build_chat_request(messages)
            )
            response = self.client.chat(chat_detail)
            return response.data.chat_response.choices[0].message.content[0].text
        except Exception as e:
            logger.error(f"Error calling Grok API: {str(e)}")
            raise
    
    def analyze_diagram(self, image_data_uri: str, prompt: str) -> str:
        """
        Analyze a diagram using Grok vision capabilities
        
        Args:
            image_data_uri: Base64 encoded image data URI
            prompt: Analysis prompt
            
        Returns:
            Analysis result text
        """
        image_content = ImageContent(image_url=ImageUrl(url=image_data_uri))
        text_content = TextContent(text=prompt)
        message = Message(role="USER", content=[image_content, text_content])
        #return self.call_grok([message])
        return self.call_llama([message])
    
    def generate_text_content(self, prompt: str) -> str:
        """
        Generate text content using Grok
        
        Args:
            prompt: Text generation prompt
            
        Returns:
            Generated text content
        """
        message = Message(role="USER", content=[TextContent(text=prompt)])
        return self.call_llama([message])

