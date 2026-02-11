# SoW Orchestration V2 (Deterministic, Structured)

## 1) Updated Data Models

### Section Plan Schema
```json
{
  "name": "Future State Architecture",
  "intent": "Describe target platform architecture",
  "category": "technical",
  "clause_filters": {
    "section": "Future State Architecture",
    "clause_type": ["architecture_guidance"],
    "tags": ["networking", "security", "identity"],
    "risk_level": ["medium"],
    "industry": "finserv",
    "region": "eu",
    "deployment_model": "saas",
    "architecture_type": "hub_spoke",
    "compliance": ["gdpr", "iso27001"]
  },
  "output_schema": {
    "overview": "",
    "architecture_pattern": "",
    "core_components": [],
    "data_flow": "",
    "security_model": "",
    "multi_tenancy_model": "",
    "limitations": ""
  }
}
```

### Structured Context Extraction Schema
```json
{
  "deployment_model": "",
  "architecture_pattern": "",
  "data_isolation_model": "",
  "cloud_provider": "",
  "ai_services_used": [],
  "data_flow_direction": "",
  "regulatory_context": []
}
```

### Enriched KB Clause Metadata Schema
```json
{
  "section": "",
  "clause_type": "",
  "risk_level": "low|medium|high",
  "industry": [],
  "region": [],
  "deployment_model": [],
  "architecture_pattern": [],
  "service_family": [],
  "compliance_scope": [],
  "tags": []
}
```

## 2) Prompt Templates

### Context Extractor Prompt
```text
Extract structured context used by deterministic SoW retrieval.
Return strict JSON object with keys deployment_model, architecture_pattern, data_isolation_model,
cloud_provider, ai_services_used, data_flow_direction, regulatory_context.
ai_services_used must be an array and unknown fields must be null/empty arrays.
Input intake JSON: {{intake_json}}
```

### Clause Assembly Mode Prompt (legal/governance/framework)
```text
WRITER MODE: CLAUSE_ASSEMBLY.
Use ONLY retrieved clauses.
No invention allowed. No new services, obligations, or commitments.
Do not use external knowledge.
Return strict JSON that exactly matches section_schema.
section_schema: {{section_schema}}
retrieved_clauses: {{retrieved_clauses}}
structured_intake_context: {{structured_context}}
```

### Technical Synthesis Mode Prompt (architecture)
```text
WRITER MODE: TECHNICAL_SYNTHESIS.
Primary source = structured intake context.
Secondary source = retrieved clauses.
Do not invent services not in intake.
Do not contradict constraints.
No freeform retrieval.
Return strict JSON that exactly matches section_schema.
section_schema: {{section_schema}}
structured_intake_context: {{structured_context}}
retrieved_clauses: {{retrieved_clauses}}
```

### Re-tagging Prompt for Existing Clauses
```text
You are a KB metadata classifier for SoW clause chunks.
Given clause text and source hints, return strict JSON with:
section, clause_type, risk_level, industry[], region[], deployment_model[],
architecture_pattern[], service_family[], compliance_scope[], tags[].
Rules:
- Use only supported labels from taxonomy file.
- If unknown, return [] (never invent values).
- tags[] must include at least 3 specific keywords.
- Return JSON only.
Clause: {{clause_text}}
Source URI: {{source_uri}}
```

## 3) Retrieval Query Builder + Fallback

```python
def build_retrieval_query(section_name, filters, intake_context, relax_tags=False):
    query = {
        "section": section_name,
        "clause_type": filters.get("clause_type"),
        "tags": filters.get("tags"),
        "risk_level": filters.get("risk_level"),
        "industry": filters.get("industry") or intake_context.get("industry"),
        "region": filters.get("region") or intake_context.get("region"),
        "deployment_model": filters.get("deployment_model") or intake_context.get("deployment_model"),
        "architecture_type": filters.get("architecture_type") or intake_context.get("architecture_pattern"),
        "compliance": filters.get("compliance") or intake_context.get("regulatory_context")
    }
    if relax_tags:
        query.pop("tags", None)
    return {k: v for k, v in query.items() if v not in (None, "", [])}
```

Fallback rule:
1. Run strict query.
2. If clause count < 5, rerun query with tags relaxed while keeping `section` fixed.
3. Record diagnostics for strict/relaxed query and final count.

## 4) Deterministic Orchestration Pseudocode

```text
PLAN:
  extract_structured_context(intake)
  for each section:
    normalize category + output_schema
    build clause_filters using structured context

RETRIEVE:
  for each section:
    strict = build_retrieval_query(... relax_tags=False)
    clauses = retrieve(strict)
    if len(clauses) < N:
      relaxed = build_retrieval_query(... relax_tags=True)
      clauses = retrieve(relaxed)
      fallback_activated = true
    log diagnostics

ASSEMBLE:
  build deterministic blueprint from ranked clauses

WRITE:
  for each section in plan order:
    if category == template: generate_from_template()
    elif category == clause: generate_clause_mode()
    elif category == technical: generate_technical_mode()
    render markdown from structured JSON

REVIEW:
  run bounded policy checks + source mapping checks
```

## 5) Architecture JSON Schema + Markdown Rendering

```json
{
  "type": "object",
  "required": [
    "overview",
    "architecture_pattern",
    "core_components",
    "data_flow",
    "security_model",
    "multi_tenancy_model",
    "limitations"
  ],
  "properties": {
    "overview": {"type": "string"},
    "architecture_pattern": {"type": "string"},
    "core_components": {"type": "array", "items": {"type": "string"}},
    "data_flow": {"type": "string"},
    "security_model": {"type": "string"},
    "multi_tenancy_model": {"type": "string"},
    "limitations": {"type": "string"}
  }
}
```

## 6) KB Migration Plan

1. Export all existing chunks and metadata into `kb_chunks_export.jsonl`.
2. Run LLM re-tagging with strict taxonomy controls.
3. Validate metadata completeness and cardinality constraints.
4. Route failed chunks to manual curation queue.
5. Bulk upsert to Object Storage index prefix with version tag (`metadata_schema_version=v2`).
6. Rebuild retrieval index and run A/B precision validation by section.

## 7) Diagnostics / Observability Payload

```json
{
  "event": "retrieval_section_completed",
  "case_id": "...",
  "section": "Future State Architecture",
  "strict_query": {...},
  "relaxed_query": {...},
  "fallback_activated": true,
  "retrieved_clause_count": 6,
  "writer_mode": "TECHNICAL_SYNTHESIS_MODE",
  "token_usage": {
    "writer_calls": 8,
    "estimated_prompt_chars": 18234
  }
}
```

## 8) Example: FUTURE STATE ARCHITECTURE (Technical Mode)

Example deterministic JSON output:

```json
{
  "overview": "The target state deploys a SaaS workload on OCI using a hub-and-spoke network architecture with centralized ingress and shared security controls.",
  "architecture_pattern": "hub-and-spoke",
  "core_components": [
    "OCI VCN hub for shared security and ingress",
    "Spoke VCNs for application and data tiers",
    "Managed Kubernetes for stateless application services",
    "Autonomous Database for transactional data",
    "OCI IAM and Vault for identity and secrets"
  ],
  "data_flow": "Inbound traffic enters through managed edge controls, is routed to application services, and persists to isolated data stores. Outbound integrations use controlled egress with policy-based routing.",
  "security_model": "Defense-in-depth with network segmentation, least-privilege IAM policies, encrypted data at rest and in transit, and centralized logging.",
  "multi_tenancy_model": "Logical tenant isolation at application and data layers using tenant-aware access controls and scoped data partitions.",
  "limitations": "Service selections and capacity targets are constrained to the named intake services and currently approved regions."
}
```
