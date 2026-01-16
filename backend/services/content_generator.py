from typing import Dict, List, Optional
from services.oci_client import OCIGrokService
from services.document_service import DocumentService
from prompts.prompt_templates import PromptTemplates
import logging

logger = logging.getLogger(__name__)

class ContentGeneratorService:
    """Service for generating content using AI with token management"""
    
    def __init__(self):
        self.oci_service = OCIGrokService()
        self.doc_service = DocumentService()
        self.prompts = PromptTemplates()
    
    def truncate_diagram_description(self, description: str, max_chars: int = 15000) -> str:
        """
        Truncate diagram description to prevent token limit issues
        
        Args:
            description: Full diagram description
            max_chars: Maximum characters to keep (roughly 3500-4000 tokens)
            
        Returns:
            Truncated description
        """
        if len(description) <= max_chars:
            return description
        
        # Truncate and add notice
        truncated = description[:max_chars]
        # Try to cut at a sentence boundary
        last_period = truncated.rfind('.')
        if last_period > max_chars * 0.8:  # If we can find a period in the last 20%
            truncated = truncated[:last_period + 1]
        
        logger.warning(f"Diagram description truncated from {len(description)} to {len(truncated)} characters")
        return truncated + "\n\n[Note: Diagram description truncated due to length]"
    
    async def generate_content(
        self,
        document_text: str,
        customer: str,
        application: str,
        scope: str,
        impdetails: str,
        diagram_data_uri: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Generate content for all placeholders in the document
        
        Args:
            document_text: Full text from the document
            customer: Customer name
            application: Application name
            scope: Project scope
            impdetails: implementation details
            diagram_data_uri: Optional diagram data URI
            
        Returns:
            Dictionary mapping placeholders to generated content
        """
        placeholders = self.doc_service.extract_placeholders(document_text)
        replacements = {}
        # Analyze diagram if provided
        diagram_description = ""
        if diagram_data_uri:
            try:
                logger.info("Starting diagram analysis...")
                full_diagram_description = self.oci_service.analyze_diagram(
                    diagram_data_uri,
                    self.prompts.DIAGRAM_ANALYSIS
                )
                
                # Truncate diagram description to prevent token issues
                diagram_description = self.truncate_diagram_description(full_diagram_description)
                replacements["DIAGRAM_DESCRIPTION"] = diagram_description
                
                logger.info(f"Diagram analysis completed successfully (length: {len(diagram_description)} chars)")
            except Exception as e:
                logger.error(f"Error analyzing diagram: {str(e)}")
                diagram_description = "Diagram analysis failed due to processing error."
                replacements["DIAGRAM_DESCRIPTION"] = diagram_description
        
        # Generate content for remaining placeholders
        for placeholder in placeholders:
            if placeholder == "DIAGRAM_DESCRIPTION":
                continue  # Already handled
            
            try:
                logger.info(f"Generating content for placeholder: {placeholder}")
                
                # Get the prompt for this placeholder
                prompt = self.prompts.get_section_prompt(
                    placeholder, customer, application, impdetails, scope, diagram_description
                )
                
                # Check prompt length before calling API
                prompt_length = len(prompt)
                if prompt_length > 50000:  # Roughly 12,000 tokens
                    logger.warning(f"Prompt for {placeholder} is very long ({prompt_length} chars), using summarized diagram")
                    # Use a much shorter version for this placeholder
                    short_diagram = diagram_description[:5000] if diagram_description else ""
                    prompt = self.prompts.get_section_prompt(
                        placeholder, customer, application, impdetails, scope, short_diagram
                    )
                
                content = self.oci_service.generate_text_content(prompt)
                replacements[placeholder] = content
                logger.info(f"Generated content for placeholder: {placeholder} (length: {len(content)} chars)")
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Error generating content for {placeholder}: {error_msg}")
                
                # Check if it's a token limit error
                if "maximum prompt length" in error_msg.lower() or "462564 tokens" in error_msg:
                    logger.warning(f"Token limit exceeded for {placeholder}, retrying with minimal context")
                    try:
                        # Retry with minimal diagram description
                        minimal_prompt = self.prompts.get_section_prompt(
                            placeholder, customer, application, impdetails, scope, ""
                        )
                        content = self.oci_service.generate_text_content(minimal_prompt)
                        replacements[placeholder] = content
                        logger.info(f"Successfully generated {placeholder} with minimal context")
                    except Exception as retry_error:
                        logger.error(f"Retry failed for {placeholder}: {str(retry_error)}")
                        replacements[placeholder] = f"Error: Content generation failed due to length constraints"
                else:
                    replacements[placeholder] = f"Error generating content for {placeholder}"
        
        return replacements
