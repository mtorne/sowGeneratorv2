from typing import Dict

class PromptTemplates:
    """Container for all prompt templates used in the application"""
    
    # Architecture diagram analysis prompt
#    DIAGRAM_ANALYSIS = """
#You are an OCI Cloud Architect. Describe the architecture in this diagram:
#
#Group services logically (Compute, Networking, etc.) based on the components explicitly shown in the diagram.
#For each service, include:
#
#Name
#What it does
#Its role in the architecture
#
#Use Markdown formatting with the following strict structure:
#
#Use bullet points (•) for main service categories (e.g., Networking, Compute and Containers) without initial hyphens.
#Use a detailed description for each service under its category, including:
#
#The service name in bold (Name).
#A brief explanation of what it does, limited to the diagram's depiction without assuming additional details.
#
#For sub-components within a service (if applicable), use indented bullet points (-) with their names and roles, ensuring proper alignment.
#
#Include all components explicitly labeled in the diagram 
#Do not include assumptions, inferred details (e.g., specific CIDR blocks or IP ranges), the phrase "Followed by its specific role in the architecture, based solely on the diagram without additional text," or any speculative roles beyond the diagram.
#Ensure consistent indentation, use only the exact labels present in the diagram, and apply bold formatting (Name) to all service names.
#"""

    DIAGRAM_ANALYSIS = """
You are an OCI Cloud Architect. Describe the architecture in this diagram:
Group services logically (Compute, Networking, etc.) based on the components explicitly shown in the diagram.
Don't provide conclusion , just architecture description for potential  readers without OCI knowledge.
Provide a brief description of the services mentioned, specially the  regional services that appear in the diagram, key highlights
"""

    # Specific section prompts
    SECTION_PROMPTS = {
        "proposed_solution": """
Generate a proposed solution section with the following structure:
Use bullet points for:
- Desired outcome
- Scope boundaries  
- Joint goals

Focus on technical feasibility and business value from an OCI Cloud Architect perspective.
only 2 lines
""",
        
        "isv_detail": """
Generate a two-line description about the ISV background for {customer}.
Include:
- Company overview and industry focus
- Technical capabilities and market position
only 1 line
""",
        
        "application_detail": """
Generate a two-line description about the application details for {application} from {customer}.
Include:
- Application type and primary functionality
- Technology stack and current deployment model
only 2 lines
""",
        
        "scope": """
Generate a two-line definition of the project scope for {application} from {customer} from the OCI team's point of view.
Include:
- Technical scope and boundaries
- Expected deliverables and success criteria
""",
       
       "ARCH_DEP_OVERVIEW": """
Based on the provided inputs, generate a high-level **Architecture and Deployment Overview** section for a Statement of Work (SoW).

Architecture Description: {diagram_description}
Implementation Details: {impdetails}
Customer: {customer}
Scope: {scope}
Application: {application}
Cloud Provider: OCI

Use a formal third-person tone suitable for a customer-facing SoW.  
Focus on describing the deployment approach, architecture components, and OCI (or the specified cloud provider) services used.  

Assume that any systems or services located outside of the OCI  regions or tenancies are **third-party integrations**.

**Requirements:**
- Provide a concise, high-level overview (maximum two paragraphs).  
- Emphasize cloud architecture structure, networking, and service integration.  
- Highlight best practices for deployment, scalability, and security.  
- Avoid overly technical details or step-by-step procedures.

Example tone:
> The solution is deployed within a secure cloud tenancy using segregated network subnets for administration, application, and database layers. Connectivity to third-party systems is achieved via secure VPN and gateway services, ensuring controlled data flow and compliance with security policies.

Output should be clear, consistent, and suitable for direct inclusion in the SoW document.
""",

        "IMP_DETAILS": """
Describe detailed implementation specifics for the '{application}' project at {customer} based on this architecture:

Architecture Description:
{diagram_description}
And implementation details {impdetails}
Provide actionable technical guidance from an OCI Cloud Architect perspective.
Use always third-person , all the services outside of the OCI Cloud Regions or tenancies are considered as a third party integrations
only 2 lines for all the detils .

Separate by meaningful groups, like networking, compute and storage , etc...  Put in bullets style

"""
    }
    
    # Default prompt template
    DEFAULT_PROMPT = """
Generate content for section '{placeholder}' from the OCI Cloud Architect point of view for a lift-and-shift validation project.

Context:
- Customer: {customer}
- Application: {application}
- Focus on technical accuracy and OCI best practices
- Provide actionable insights and recommendations
"""

    @classmethod
    def get_section_prompt(cls, placeholder: str, customer: str, application: str, impdetails: str, scope: str, diagram_description: str = "") -> str:
        """
        Get the appropriate prompt for a given placeholder section
        
        Args:
            placeholder: The section placeholder name
            customer: Customer name
            application: Application name
            impdetails: Implementation Details
            diagram_description: Architecture diagram description (if available)
            
        Returns:
            Formatted prompt string
        """
        if placeholder in cls.SECTION_PROMPTS:
            prompt_template = cls.SECTION_PROMPTS[placeholder]
            return prompt_template.format(
                customer=customer,
                application=application,
                impdetails=impdetails,
                scope=scope,
                diagram_description=diagram_description
            )
        else:
            return cls.DEFAULT_PROMPT.format(
                placeholder=placeholder,
                customer=customer,
                impdetails=impdetails,
                scope=scope,
                application=application
            )

