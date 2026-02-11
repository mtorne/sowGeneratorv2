# SoW Backend Refactor (Deterministic Structured Workflow)

## Deterministic orchestration

```text
EXTRACT -> PLAN -> RETRIEVE -> RERANK -> WRITE -> VALIDATE -> RENDER
```

## Clause metadata schema (KB entries)

```json
{
  "id": "clause-123",
  "text": "...",
  "section": "FUTURE STATE ARCHITECTURE",
  "clause_type": "constraint",
  "risk_level": "high",
  "industry": "retail",
  "region": "EU",
  "deployment_model": "hybrid",
  "architecture_pattern": "event-driven",
  "service_family": "integration",
  "compliance_scope": "GDPR",
  "tags": ["security", "isolation"]
}
```

## Example section definition (FUTURE STATE ARCHITECTURE)

```json
{
  "name": "FUTURE STATE ARCHITECTURE",
  "intent": "Describe target architecture and operating constraints.",
  "category": "technical",
  "clause_filters": {
    "clause_type": ["constraint", "control"],
    "risk_level": ["high", "medium"],
    "tags": ["architecture", "security", "isolation"],
    "service_family": ["ai", "integration"],
    "compliance_scope": ["GDPR"]
  },
  "required_fields": [
    "overview",
    "architecture_pattern",
    "core_components",
    "data_flow",
    "security_model",
    "multi_tenancy_model",
    "limitations"
  ],
  "min_content": {
    "overview": {"min_words": 40},
    "core_components": {"min_items": 4},
    "data_flow": {"min_words": 25},
    "security_model": {"min_words": 20}
  },
  "fallback_policy": {
    "min_clauses": 3,
    "relaxation_order": ["tags", "industry", "region", "risk_level"],
    "max_retries": 3
  }
}
```

## Example architecture JSON output

```json
{
  "overview": "The future-state solution uses a hybrid OCI architecture for EU retail operations, where ingestion and inference workloads run in OCI and connect securely to existing on-premises retail systems. OCI AI Services and OCI Data Science are used for model-driven processing under strict customer isolation controls.",
  "architecture_pattern": "hybrid event-driven integration",
  "core_components": [
    "OCI API Gateway",
    "OCI Functions",
    "OCI AI Services",
    "OCI Data Science",
    "OCI Object Storage",
    "Private connectivity to on-premises systems"
  ],
  "data_flow": "Bidirectional data exchange is enforced: curated retail signals and customer activity are sent to OCI for processing, while predictions and scoring outputs are returned to on-premises systems over approved private channels with policy-controlled interfaces.",
  "security_model": "Strict customer isolation is implemented through tenancy-level and compartment-level controls, with per-customer data partitioning, IAM policies, and encryption for data at rest and in transit aligned to EU regulatory context.",
  "multi_tenancy_model": "Strict customer isolation with segmented tenancy/compartment boundaries and no cross-customer data mixing.",
  "limitations": "Service scope is limited to OCI AI Services or OCI Data Science as approved AI platforms; additional third-party AI services require explicit approval and updated compliance review."
}
```

## Example diagnostics output (one run)

```json
{
  "run_id": "case-123",
  "extractedContext": {
    "deployment_model": "hybrid",
    "architecture_pattern": "hybrid event-driven integration",
    "data_isolation_model": "strict customer isolation",
    "cloud_provider": "OCI",
    "ai_services_used": ["OCI AI Services", "OCI Data Science"],
    "data_flow_direction": "bidirectional",
    "regulatory_context": ["GDPR"],
    "industry": "retail",
    "region": "EU",
    "allowed_services": ["OCI AI Services", "OCI Data Science", "Object Storage"]
  },
  "retrieval": {
    "FUTURE STATE ARCHITECTURE": {
      "attempts": [
        {"attempt": 1, "filters_used": {"section": "FUTURE STATE ARCHITECTURE", "industry": "retail", "region": "EU", "deployment_model": "hybrid", "tags": ["architecture", "security"]}, "returned_count": 1},
        {"attempt": 2, "filters_used": {"section": "FUTURE STATE ARCHITECTURE", "industry": "retail", "region": "EU", "deployment_model": "hybrid"}, "returned_count": 4}
      ],
      "pre_rerank_count": 4,
      "post_rerank_count": 4
    }
  },
  "writer_mode": "TECHNICAL_SYNTHESIS_MODE",
  "validation": {"pass": true, "reasons": []},
  "token_usage": {"writer_calls": 1, "estimated_prompt_chars": 3142}
}
```
