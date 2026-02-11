from typing import Dict


class PromptTemplates:
    """Container for all prompt templates used in the application."""

    # Deterministic SoW workflow prompts
    CONTEXT_EXTRACTION_PROMPT = """
You are the EXTRACT stage for a deterministic SoW pipeline.
Return strict JSON only with keys:
- deployment_model
- architecture_pattern
- data_isolation_model
- cloud_provider
- ai_services_used (array)
- data_flow_direction
- regulatory_context (array)
- industry
- region
- allowed_services (array)
Rules:
1) Do not include markdown or commentary.
2) Unknown values must be null (or empty array for array fields).
3) allowed_services must be derived from intake and ai_services_used.
Intake JSON: {intake_json}
""".strip()

    CLAUSE_ASSEMBLY_PROMPT = """
WRITER MODE: CLAUSE_ASSEMBLY
You are in WRITE stage.
Primary and only content source: retrieved_clauses.
Rules:
1) Use ONLY retrieved clauses as building blocks.
2) Rephrase for coherence if needed.
3) Do NOT introduce new obligations, services, or commitments.
4) Output strict JSON exactly matching section_schema.
Section: {section_name}
Intent: {section_intent}
Section Schema: {section_schema}
Structured Context: {structured_context}
Retrieved Clauses: {retrieved_clauses}
""".strip()

    TECHNICAL_SYNTHESIS_PROMPT = """
WRITER MODE: TECHNICAL_SYNTHESIS
You are in WRITE stage.
Primary source: extracted structured context and allowed_services.
Secondary source: retrieved clauses for constraints and standard wording.
Rules:
1) Do NOT introduce services outside allowed_services.
2) Do NOT contradict extracted context constraints.
3) architecture_pattern must match extracted_context.architecture_pattern.
4) Output strict JSON exactly matching section_schema.
Section: {section_name}
Intent: {section_intent}
Section Schema: {section_schema}
Structured Context: {structured_context}
Retrieved Clauses: {retrieved_clauses}
""".strip()

    REVIEWER_VALIDATOR_PROMPT = """
You are a deterministic validator for SoW section JSON.
Given section_definition, extracted_context, and section_json:
1) Check required_fields are present and non-empty.
2) Check min_content thresholds.
3) For technical sections, check architecture_pattern equality and allowed_services compliance.
Return strict JSON:
{
  "pass": true|false,
  "reasons": []
}
section_definition: {section_definition}
extracted_context: {extracted_context}
section_json: {section_json}
""".strip()

    # Legacy document-generation prompts (used by ContentGeneratorService)
    DIAGRAM_ANALYSIS = """
You are an OCI Cloud Architect. Describe the architecture in this diagram:
Group services logically (Compute, Networking, etc.) based on the components explicitly shown in the diagram.
Don't provide conclusion, just architecture description for potential readers without OCI knowledge.
Provide a brief description of the services mentioned, especially regional services that appear in the diagram.
""".strip()

    SECTION_PROMPTS: Dict[str, str] = {
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
Assume that systems outside OCI cloud regions/tenancies are third-party integrations.

Requirements:
- Provide a concise, high-level overview (maximum two paragraphs).
- Emphasize cloud architecture structure, networking, and service integration.
- Highlight best practices for deployment, scalability, and security.
- Avoid overly technical details or step-by-step procedures.
""",
        "IMP_DETAILS": """
Describe detailed implementation specifics for the '{application}' project at {customer} based on this architecture:

Architecture Description:
{diagram_description}
And implementation details {impdetails}
Provide actionable technical guidance from an OCI Cloud Architect perspective.
Use third-person language. Services outside OCI regions/tenancies are third-party integrations.
Only 2 lines for all details.

Separate by meaningful groups, like networking, compute and storage, etc. Use bullet style.
""",
    }

    DEFAULT_PROMPT = """
Generate content for section '{placeholder}' from the OCI Cloud Architect point of view for a lift-and-shift validation project.

Context:
- Customer: {customer}
- Application: {application}
- Focus on technical accuracy and OCI best practices
- Provide actionable insights and recommendations
"""

    @classmethod
    def get_section_prompt(
        cls,
        placeholder: str,
        customer: str,
        application: str,
        impdetails: str,
        scope: str,
        diagram_description: str = "",
    ) -> str:
        if placeholder in cls.SECTION_PROMPTS:
            prompt_template = cls.SECTION_PROMPTS[placeholder]
            return prompt_template.format(
                customer=customer,
                application=application,
                impdetails=impdetails,
                scope=scope,
                diagram_description=diagram_description,
            )
        return cls.DEFAULT_PROMPT.format(
            placeholder=placeholder,
            customer=customer,
            application=application,
            impdetails=impdetails,
            scope=scope,
        )
