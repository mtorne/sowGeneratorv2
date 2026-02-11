from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import json
import logging
import re
from uuid import uuid4

from services.knowledge_access_service import KnowledgeAccessService
from services.oci_client import OCIGenAIService
from prompts.prompt_templates import PromptTemplates

logger = logging.getLogger(__name__)


class WorkflowStage(str, Enum):
    INIT = "INIT"
    EXTRACTED = "EXTRACTED"
    PLAN_READY = "PLAN_READY"
    RETRIEVED = "RETRIEVED"
    RERANKED = "RERANKED"
    ASSEMBLED = "ASSEMBLED"
    DRAFTED = "DRAFTED"
    VALIDATED = "VALIDATED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"


@dataclass
class WorkflowArtifact:
    stage: WorkflowStage
    version: int
    created_at: str
    payload: Dict[str, Any]


@dataclass
class SOWCase:
    case_id: str
    created_at: str
    intake: Dict[str, Any]
    stage: WorkflowStage = WorkflowStage.INIT
    artifacts: Dict[WorkflowStage, List[WorkflowArtifact]] = field(default_factory=dict)


@dataclass
class FallbackPolicy:
    min_clauses: int = 3
    relaxation_order: List[str] = field(default_factory=lambda: ["tags", "industry", "region", "risk_level"])
    max_retries: int = 3


@dataclass
class SectionDefinition:
    name: str
    intent: str
    category: str = "clause"  # template | clause | technical
    clause_filters: Dict[str, Any] = field(default_factory=dict)
    required_fields: List[str] = field(default_factory=list)
    min_content: Dict[str, Dict[str, int]] = field(default_factory=dict)
    fallback_policy: FallbackPolicy = field(default_factory=FallbackPolicy)
    output_schema: Dict[str, Any] = field(default_factory=dict)


class WorkflowError(Exception):
    pass


class StateTransitionError(WorkflowError):
    pass


class ValidationError(WorkflowError):
    pass


class SOWWorkflowService:
    """Deterministic orchestration service for EXTRACT->PLAN->RETRIEVE->RERANK->WRITE->VALIDATE->RENDER."""

    CLAUSE_METADATA_FIELDS = [
        "id",
        "text",
        "section",
        "clause_type",
        "risk_level",
        "industry",
        "region",
        "deployment_model",
        "architecture_pattern",
        "service_family",
        "compliance_scope",
        "tags",
    ]
    RETRIEVAL_FILTER_FIELDS = [
        "section",
        "clause_type",
        "tags",
        "risk_level",
        "industry",
        "region",
        "deployment_model",
        "architecture_pattern",
        "service_family",
        "compliance_scope",
    ]
    DEFAULT_SECTION_SCHEMAS = {
        "clause": {
            "section_summary": "",
            "obligations": [],
            "constraints": [],
            "limitations": [],
        },
        "technical": {
            "overview": "",
            "architecture_pattern": "",
            "core_components": [],
            "data_flow": "",
            "security_model": "",
            "multi_tenancy_model": "",
            "limitations": "",
        },
        "template": {
            "content": "",
        },
    }

    def __init__(
        self,
        knowledge_access_service: KnowledgeAccessService | None = None,
        llm_service: OCIGenAIService | None = None,
    ) -> None:
        self._cases: Dict[str, SOWCase] = {}
        self.knowledge_access_service = knowledge_access_service or KnowledgeAccessService()
        self.llm_service = llm_service or OCIGenAIService()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_case(self, intake: Dict[str, Any]) -> SOWCase:
        self._validate_intake(intake)
        case_id = str(uuid4())
        sow_case = SOWCase(case_id=case_id, created_at=self._now_iso(), intake=dict(intake))
        self._cases[case_id] = sow_case
        self.run_extract(case_id)
        return sow_case

    def get_case(self, case_id: str) -> SOWCase:
        if case_id not in self._cases:
            raise ValidationError(f"Unknown case_id '{case_id}'")
        return self._cases[case_id]

    def run_extract(self, case_id: str) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.INIT, WorkflowStage.EXTRACTED])
        extracted_context = self._extract_structured_context(sow_case.intake)
        sow_case.intake["structured_context"] = extracted_context
        artifact = self._append_artifact(sow_case, WorkflowStage.EXTRACTED, {"extracted_context": extracted_context})
        sow_case.stage = WorkflowStage.EXTRACTED
        self._diag(event="extracted_context", case_id=case_id, extracted_context=extracted_context)
        return artifact

    def run_plan(self, case_id: str, plan_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        if "structured_context" not in sow_case.intake:
            self.run_extract(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.EXTRACTED, WorkflowStage.PLAN_READY])

        sections = plan_input.get("sections") or []
        if not sections:
            raise ValidationError("PLAN requires non-empty sections")

        extracted = sow_case.intake.get("structured_context") or {}
        normalized_sections: List[Dict[str, Any]] = []
        retrieval_specs: List[Dict[str, Any]] = []

        for raw in sections:
            if not raw.get("name") or not raw.get("intent"):
                raise ValidationError("Each plan section requires name and intent")
            section_def = self._normalize_section_definition(raw, sow_case.intake, extracted)
            section_payload = self._section_definition_to_payload(section_def)
            normalized_sections.append(section_payload)
            retrieval_specs.append(
                {
                    "section": section_def.name,
                    "category": section_def.category,
                    "clause_filters": section_def.clause_filters,
                    "fallback_policy": {
                        "min_clauses": section_def.fallback_policy.min_clauses,
                        "relaxation_order": section_def.fallback_policy.relaxation_order,
                        "max_retries": section_def.fallback_policy.max_retries,
                    },
                }
            )

        artifact = self._append_artifact(
            sow_case,
            WorkflowStage.PLAN_READY,
            {
                "plan": {
                    "sections": normalized_sections,
                    "retrieval_specs": retrieval_specs,
                    "structured_context": extracted,
                    "risk_checks": plan_input.get("risk_checks", []),
                }
            },
        )
        sow_case.stage = WorkflowStage.PLAN_READY
        return artifact

    def run_retrieve(self, case_id: str, retrieve_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.PLAN_READY, WorkflowStage.RETRIEVED])

        plan = self.get_latest_artifact(case_id, WorkflowStage.PLAN_READY).payload["plan"]
        section_defs = {s["name"]: s for s in plan.get("sections", [])}
        top_k = max(1, int(retrieve_input.get("top_k", 8)))
        allow_partial = bool(retrieve_input.get("allow_partial", False))
        selected_names = set(retrieve_input.get("section_names") or section_defs.keys())

        retrieval_set: Dict[str, List[Dict[str, Any]]] = {}
        diagnostics: Dict[str, Any] = {}
        extracted = sow_case.intake.get("structured_context") or {}

        for section_name, section_payload in section_defs.items():
            if section_name not in selected_names:
                continue
            section_def = self._payload_to_section_definition(section_payload)
            clauses, section_diag = self._retrieve_with_fallback(section_def, extracted, top_k)
            retrieval_set[section_name] = clauses
            diagnostics[section_name] = section_diag

        if not all(retrieval_set.values()) and not allow_partial:
            raise ValidationError("RETRIEVE produced insufficient section coverage")

        if allow_partial:
            retrieval_set = {k: v for k, v in retrieval_set.items() if v}

        artifact = self._append_artifact(
            sow_case,
            WorkflowStage.RETRIEVED,
            {
                "retrieval_set": retrieval_set,
                "diagnostics": diagnostics,
                "meta": {
                    "requested_sections": list(selected_names),
                    "returned_sections": list(retrieval_set.keys()),
                    "top_k": top_k,
                },
            },
        )
        sow_case.stage = WorkflowStage.RETRIEVED
        return artifact

    def run_assemble(self, case_id: str) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.RETRIEVED, WorkflowStage.RERANKED, WorkflowStage.ASSEMBLED])
        retrieval_payload = self.get_latest_artifact(case_id, WorkflowStage.RETRIEVED).payload
        plan = self.get_latest_artifact(case_id, WorkflowStage.PLAN_READY).payload["plan"]
        section_defs = {s["name"]: s for s in plan.get("sections", [])}

        top_m = 4
        rerank_diagnostics: Dict[str, Any] = {}
        blueprint: Dict[str, Any] = {}

        for section_name, clauses in retrieval_payload.get("retrieval_set", {}).items():
            section_cfg = section_defs.get(section_name, {})
            category = section_cfg.get("category", "clause")
            query = {
                "section": section_name,
                "intent": section_cfg.get("intent"),
                "structured_context": plan.get("structured_context", {}),
            }
            reranked = self.rerank_clauses(query, clauses)[:top_m]
            blueprint[section_name] = {
                "section_intent": section_cfg.get("intent", ""),
                "category": category,
                "order": [c.get("chunk_id") for c in reranked],
                "primary_clause_ids": [c.get("chunk_id") for c in reranked[:2]],
                "primary_clauses": reranked[:2],
                "reranked_clauses": reranked,
                "output_schema": section_cfg.get("output_schema") or self.DEFAULT_SECTION_SCHEMAS.get(category, {}),
                "required_fields": section_cfg.get("required_fields", []),
                "min_content": section_cfg.get("min_content", {}),
                "fallback_policy": section_cfg.get("fallback_policy", {}),
                "clause_filters": section_cfg.get("clause_filters", {}),
            }
            rerank_diagnostics[section_name] = {
                "pre_rerank_count": len(clauses),
                "post_rerank_count": len(reranked),
                "top_m": top_m,
            }

        reranked_artifact = self._append_artifact(
            sow_case,
            WorkflowStage.RERANKED,
            {"retrieval_set": retrieval_payload.get("retrieval_set", {}), "diagnostics": rerank_diagnostics},
        )
        _ = reranked_artifact
        artifact = self._append_artifact(sow_case, WorkflowStage.ASSEMBLED, {"assembly_blueprint": blueprint})
        sow_case.stage = WorkflowStage.ASSEMBLED
        return artifact

    def run_write(self, case_id: str, write_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.ASSEMBLED, WorkflowStage.DRAFTED, WorkflowStage.VALIDATED])

        blueprint = self.get_latest_artifact(case_id, WorkflowStage.ASSEMBLED).payload["assembly_blueprint"]
        plan = self.get_latest_artifact(case_id, WorkflowStage.PLAN_READY).payload["plan"]
        extracted = sow_case.intake.get("structured_context") or {}
        style = write_input.get("style", "professional")
        prohibited = write_input.get("prohibited_commitments", [])

        structured_sections: List[Dict[str, Any]] = []
        run_diag = {
            "extracted_context": extracted,
            "sections": {},
            "token_usage": {"writer_calls": 0, "estimated_prompt_chars": 0},
        }

        for section_cfg in plan.get("sections", []):
            section_name = section_cfg["name"]
            section_def = self._payload_to_section_definition(section_cfg)
            assembled = blueprint.get(section_name, {})
            candidates = assembled.get("reranked_clauses") or assembled.get("primary_clauses") or []
            writer_mode = self._resolve_writer_mode(section_def.category)

            output, validation_diag = self._write_with_validation_retry(
                section_def=section_def,
                section_name=section_name,
                section_intent=section_cfg.get("intent", ""),
                style=style,
                candidates=candidates,
                intake=sow_case.intake,
                extracted_context=extracted,
            )

            markdown = self._render_section_markdown(section_name, section_def.category, output)
            if any(word.lower() in markdown.lower() for word in prohibited):
                raise ValidationError(f"WRITE produced prohibited commitment language in section '{section_name}'")

            run_diag["token_usage"]["writer_calls"] += 1
            run_diag["token_usage"]["estimated_prompt_chars"] += len(json.dumps(output))
            run_diag["sections"][section_name] = {
                "writer_mode": writer_mode,
                "validation": validation_diag,
            }

            structured_sections.append(
                {
                    "name": section_name,
                    "intent": section_cfg.get("intent", ""),
                    "category": section_def.category,
                    "writer_mode": writer_mode,
                    "structured_content": output,
                    "draft_markdown": markdown,
                    "source_mapping": [{"paragraph": 1, "clause_ids": [c.get("chunk_id") for c in candidates[:2]]}],
                }
            )

        markdown = self._build_document_markdown(sow_case, structured_sections)
        draft_artifact = self._append_artifact(
            sow_case,
            WorkflowStage.DRAFTED,
            {
                "draft": {
                    "structured_sections": structured_sections,
                    "sections_json": {item["name"]: item["structured_content"] for item in structured_sections},
                    "markdown": markdown,
                },
                "diagnostics": run_diag,
            },
        )
        sow_case.stage = WorkflowStage.DRAFTED

        validate_artifact = self.run_validate(case_id)
        _ = validate_artifact
        return draft_artifact

    def run_validate(self, case_id: str) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.DRAFTED, WorkflowStage.VALIDATED])
        draft_sections = self.get_latest_artifact(case_id, WorkflowStage.DRAFTED).payload["draft"]["structured_sections"]
        plan = self.get_latest_artifact(case_id, WorkflowStage.PLAN_READY).payload["plan"]
        extracted = sow_case.intake.get("structured_context") or {}
        section_cfg = {s["name"]: s for s in plan.get("sections", [])}
        findings = []

        for section in draft_sections:
            cfg = self._payload_to_section_definition(section_cfg.get(section["name"], {"name": section["name"], "intent": ""}))
            valid, reasons = self.validate_section_output(cfg, section.get("structured_content", {}), extracted)
            if not valid:
                findings.append({"section": section["name"], "reasons": reasons})

        status = "pass" if not findings else "fail"
        artifact = self._append_artifact(sow_case, WorkflowStage.VALIDATED, {"validation_report": {"status": status, "findings": findings}})
        sow_case.stage = WorkflowStage.VALIDATED
        return artifact

    def run_review(self, case_id: str, review_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.VALIDATED, WorkflowStage.REVIEWED])
        draft = self.get_latest_artifact(case_id, WorkflowStage.DRAFTED).payload["draft"]["structured_sections"]
        findings = []
        forbidden_phrases = review_input.get("forbidden_phrases", ["guarantee", "without exception"])
        for section_data in draft:
            text = section_data.get("draft_markdown", "").lower()
            for phrase in forbidden_phrases:
                if phrase.lower() in text:
                    findings.append(
                        {
                            "severity": "critical",
                            "type": "risk",
                            "section": section_data["name"],
                            "evidence": phrase,
                            "recommendation": "Replace absolute commitments with bounded language",
                        }
                    )
            if not section_data.get("source_mapping"):
                findings.append(
                    {
                        "severity": "critical",
                        "type": "grounding",
                        "section": section_data["name"],
                        "evidence": "missing source mapping",
                        "recommendation": "Add clause mapping for each paragraph",
                    }
                )

        status = "pass" if not any(f.get("severity") == "critical" for f in findings) else "fail"
        artifact = self._append_artifact(
            sow_case,
            WorkflowStage.REVIEWED,
            {"review_report": {"status": status, "findings": findings}},
        )
        sow_case.stage = WorkflowStage.REVIEWED
        return artifact

    def approve(self, case_id: str) -> SOWCase:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.REVIEWED])
        review = self.get_latest_artifact(case_id, WorkflowStage.REVIEWED).payload["review_report"]
        if review["status"] != "pass":
            raise StateTransitionError("Cannot APPROVE when review status is fail")
        sow_case.stage = WorkflowStage.APPROVED
        return sow_case

    def render_document_markdown(self, case_id: str) -> str:
        sow_case = self.get_case(case_id)
        if sow_case.stage not in [WorkflowStage.DRAFTED, WorkflowStage.VALIDATED, WorkflowStage.REVIEWED, WorkflowStage.APPROVED]:
            raise ValidationError("Document is available only after WRITE stage")
        draft = self.get_latest_artifact(case_id, WorkflowStage.DRAFTED).payload["draft"]
        return draft.get("markdown", "")

    def render_document_html(self, case_id: str) -> str:
        markdown = self.render_document_markdown(case_id)
        html_lines = [
            "<!DOCTYPE html>",
            "<html><head><meta charset='utf-8'><title>SoW Document</title>",
            "<style>body{font-family:Arial,sans-serif;max-width:900px;margin:24px auto;line-height:1.5;padding:0 12px;}h1,h2{color:#0f172a;}hr{border:0;border-top:1px solid #ddd;}ul{padding-left:22px;}</style>",
            "</head><body>",
        ]
        for line in markdown.splitlines():
            if line.startswith("# "):
                html_lines.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("- "):
                html_lines.append(f"<p>{line}</p>")
            elif line.strip() == "---":
                html_lines.append("<hr />")
            elif line.strip() == "":
                html_lines.append("<br />")
            else:
                html_lines.append(f"<p>{line}</p>")
        html_lines.append("</body></html>")
        return "\n".join(html_lines)

    def get_latest_artifact(self, case_id: str, stage: WorkflowStage) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        artifacts = sow_case.artifacts.get(stage, [])
        if not artifacts:
            raise ValidationError(f"No artifact found for stage {stage}")
        return artifacts[-1]

    def _append_artifact(self, sow_case: SOWCase, stage: WorkflowStage, payload: Dict[str, Any]) -> WorkflowArtifact:
        artifact_list = sow_case.artifacts.setdefault(stage, [])
        artifact = WorkflowArtifact(stage=stage, version=len(artifact_list) + 1, created_at=self._now_iso(), payload=payload)
        artifact_list.append(artifact)
        return artifact

    @staticmethod
    def _ensure_stage(sow_case: SOWCase, allowed_stages: List[WorkflowStage]) -> None:
        if sow_case.stage not in allowed_stages:
            allowed = ", ".join(stage.value for stage in allowed_stages)
            raise StateTransitionError(f"Invalid stage transition from {sow_case.stage.value}. Expected one of: {allowed}")

    @staticmethod
    def _validate_intake(intake: Dict[str, Any]) -> None:
        required = ["client_name", "project_scope", "document_type", "industry", "region"]
        missing = [field for field in required if not intake.get(field)]
        if missing:
            raise ValidationError(f"Missing intake fields: {', '.join(missing)}")

    def _extract_structured_context(self, intake: Dict[str, Any]) -> Dict[str, Any]:
        prompt = PromptTemplates.CONTEXT_EXTRACTION_PROMPT.format(intake_json=json.dumps(intake))
        response_format = {
            "type": "JSON_SCHEMA",
            "json_schema": {
                "name": "sow_context_extraction",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "deployment_model": {"type": ["string", "null"]},
                        "architecture_pattern": {"type": ["string", "null"]},
                        "data_isolation_model": {"type": ["string", "null"]},
                        "cloud_provider": {"type": ["string", "null"]},
                        "ai_services_used": {"type": "array", "items": {"type": "string"}},
                        "data_flow_direction": {"type": ["string", "null"]},
                        "regulatory_context": {"type": "array", "items": {"type": "string"}},
                        "industry": {"type": ["string", "null"]},
                        "region": {"type": ["string", "null"]},
                        "allowed_services": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "deployment_model",
                        "architecture_pattern",
                        "data_isolation_model",
                        "cloud_provider",
                        "ai_services_used",
                        "data_flow_direction",
                        "regulatory_context",
                        "industry",
                        "region",
                        "allowed_services",
                    ],
                    "additionalProperties": False,
                },
            },
        }
        parsed = {}
        try:
            response = self.llm_service.generate_text_content(prompt=prompt, provider="generic", response_format=response_format)
            parsed = self._parse_json_object(response)
        except Exception as exc:
            logger.warning("Structured context extraction failed, using fallback defaults: %s", exc)

        ai_services = parsed.get("ai_services_used") or intake.get("ai_services_used") or []
        cloud_provider = parsed.get("cloud_provider") or intake.get("cloud_provider") or "OCI"
        allowed_services = parsed.get("allowed_services") or self._derive_allowed_services(ai_services, cloud_provider)

        return {
            "deployment_model": parsed.get("deployment_model") or intake.get("deployment_model"),
            "architecture_pattern": parsed.get("architecture_pattern") or intake.get("architecture_pattern"),
            "data_isolation_model": parsed.get("data_isolation_model") or intake.get("data_isolation_model"),
            "cloud_provider": cloud_provider,
            "ai_services_used": ai_services,
            "data_flow_direction": parsed.get("data_flow_direction") or intake.get("data_flow_direction"),
            "regulatory_context": parsed.get("regulatory_context") or intake.get("regulatory_context") or [],
            "industry": parsed.get("industry") or intake.get("industry"),
            "region": parsed.get("region") or intake.get("region"),
            "allowed_services": allowed_services,
        }

    @staticmethod
    def _derive_allowed_services(ai_services: List[str], cloud_provider: str) -> List[str]:
        mapped = set(ai_services or [])
        provider = (cloud_provider or "").lower()
        if "oci" in provider:
            mapped.update(["OCI AI Services", "OCI Data Science", "Object Storage", "API Gateway", "Functions"])
        return sorted(mapped)

    @staticmethod
    def _parse_json_object(raw: str) -> Dict[str, Any]:
        if not raw:
            return {}
        raw = raw.strip()
        for candidate in [raw, *re.findall(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)]:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
        return {}

    def _normalize_section_definition(self, raw: Dict[str, Any], intake: Dict[str, Any], extracted: Dict[str, Any]) -> SectionDefinition:
        category = raw.get("category", "clause")
        if category not in {"template", "clause", "technical"}:
            raise ValidationError(f"Invalid section category '{category}'")

        clause_filters = self._resolve_structured_filters(raw.get("clause_filters") or {}, intake, extracted)
        output_schema = raw.get("output_schema") or self.DEFAULT_SECTION_SCHEMAS.get(category, {})
        fallback = raw.get("fallback_policy") or {}
        fallback_policy = FallbackPolicy(
            min_clauses=int(fallback.get("min_clauses", 3)),
            relaxation_order=list(fallback.get("relaxation_order", ["tags", "industry", "region", "risk_level"])),
            max_retries=int(fallback.get("max_retries", 3)),
        )
        return SectionDefinition(
            name=raw["name"],
            intent=raw["intent"],
            category=category,
            clause_filters=clause_filters,
            required_fields=list(raw.get("required_fields") or list(output_schema.keys())),
            min_content=raw.get("min_content") or {},
            fallback_policy=fallback_policy,
            output_schema=output_schema,
        )

    def _resolve_structured_filters(self, filters: Dict[str, Any], intake: Dict[str, Any], extracted: Dict[str, Any]) -> Dict[str, Any]:
        resolved: Dict[str, Any] = {}
        for key, value in (filters or {}).items():
            if isinstance(value, str):
                resolved[key] = self._resolve_template_value(value, intake, extracted)
            else:
                resolved[key] = value
        return {k: v for k, v in resolved.items() if k in self.RETRIEVAL_FILTER_FIELDS and v not in (None, "", [])}

    @staticmethod
    def _resolve_template_value(value: str, intake: Dict[str, Any], extracted: Dict[str, Any]) -> Any:
        template_match = re.fullmatch(r"\{\{\s*intake\.([a-zA-Z0-9_]+)\s*\}\}", value)
        if template_match:
            return intake.get(template_match.group(1))
        context_match = re.fullmatch(r"\{\{\s*structured\.([a-zA-Z0-9_]+)\s*\}\}", value)
        if context_match:
            return extracted.get(context_match.group(1))
        return value

    def build_retrieval_query(self, section_def: SectionDefinition, extracted_context: Dict[str, Any], relaxed_dimensions: Optional[List[str]] = None) -> Dict[str, Any]:
        relaxed = set(relaxed_dimensions or [])
        query = {"section": section_def.name}
        for key, val in (section_def.clause_filters or {}).items():
            if key in self.RETRIEVAL_FILTER_FIELDS and key not in relaxed and val not in (None, "", []):
                query[key] = val

        defaults = {
            "industry": extracted_context.get("industry"),
            "region": extracted_context.get("region"),
            "deployment_model": extracted_context.get("deployment_model"),
            "architecture_pattern": extracted_context.get("architecture_pattern"),
        }
        for key, value in defaults.items():
            if key not in relaxed and key not in query and value not in (None, "", []):
                query[key] = value
        return query

    def _retrieve_with_fallback(self, section_def: SectionDefinition, extracted_context: Dict[str, Any], top_k: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        attempts = []
        relaxed_dimensions: List[str] = []
        clauses: List[Dict[str, Any]] = []

        for attempt in range(section_def.fallback_policy.max_retries + 1):
            query = self.build_retrieval_query(section_def, extracted_context, relaxed_dimensions)
            clauses = self.knowledge_access_service.retrieve_section_clauses(
                section_name=section_def.name,
                filters=query,
                intake={},
                top_k=top_k,
                allow_relaxed_retry=False,
            )
            attempts.append({"attempt": attempt + 1, "filters_used": query, "returned_count": len(clauses)})
            if len(clauses) >= section_def.fallback_policy.min_clauses:
                break
            if attempt < section_def.fallback_policy.max_retries:
                relax_key = section_def.fallback_policy.relaxation_order[min(attempt, len(section_def.fallback_policy.relaxation_order) - 1)]
                if relax_key != "section":
                    relaxed_dimensions.append(relax_key)

        return clauses, {"attempts": attempts, "final_count": len(clauses), "relaxed_dimensions": relaxed_dimensions}

    @staticmethod
    def rerank_clauses(query: Dict[str, Any], clauses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def clause_rank(item: Dict[str, Any]) -> Tuple[float, int]:
            score = float(item.get("score", 0.0))
            text = item.get("clause_text") or ""
            boosts = 1 if query.get("section", "").lower() in text.lower() else 0
            return (score, boosts)

        return sorted(clauses, key=clause_rank, reverse=True)

    def _write_with_validation_retry(
        self,
        section_def: SectionDefinition,
        section_name: str,
        section_intent: str,
        style: str,
        candidates: List[Dict[str, Any]],
        intake: Dict[str, Any],
        extracted_context: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        diagnostics = {"attempts": []}
        output = {}
        for attempt in range(section_def.fallback_policy.max_retries + 1):
            writer_mode = self._resolve_writer_mode(section_def.category)
            if section_def.category == "template":
                output = self._generate_from_template(section_name, section_intent, section_def.output_schema, intake)
            elif section_def.category == "technical":
                output = self._generate_technical_mode(
                    section_name=section_name,
                    section_intent=section_intent,
                    style=style,
                    section_schema=section_def.output_schema,
                    retrieved_clauses=candidates,
                    extracted_context=extracted_context,
                )
            else:
                output = self._generate_clause_mode(
                    section_name=section_name,
                    section_intent=section_intent,
                    style=style,
                    section_schema=section_def.output_schema,
                    retrieved_clauses=candidates,
                    extracted_context=extracted_context,
                )

            valid, reasons = self.validate_section_output(section_def, output, extracted_context)
            diagnostics["attempts"].append({"attempt": attempt + 1, "writer_mode": writer_mode, "pass": valid, "reasons": reasons})
            if valid:
                return output, diagnostics

            clauses, retrieval_diag = self._retrieve_with_fallback(section_def, extracted_context, top_k=8)
            candidates = self.rerank_clauses({"section": section_name}, clauses)[:4]
            diagnostics["attempts"][-1]["retrieval_retry"] = retrieval_diag

        return self._build_tbd_output(section_def), diagnostics

    def _generate_clause_mode(
        self,
        section_name: str,
        section_intent: str,
        style: str,
        section_schema: Dict[str, Any],
        retrieved_clauses: List[Dict[str, Any]],
        extracted_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = PromptTemplates.CLAUSE_ASSEMBLY_PROMPT.format(
            section_name=section_name,
            section_intent=section_intent,
            section_schema=json.dumps(section_schema),
            structured_context=json.dumps(extracted_context),
            retrieved_clauses=json.dumps(retrieved_clauses),
        ) + f"\nStyle: {style}\nRules: Rephrase only for coherence. Do not add obligations/services not in clauses."
        return self._run_writer_prompt(prompt=prompt, section_schema=section_schema, schema_name="clause_writer")

    def _generate_technical_mode(
        self,
        section_name: str,
        section_intent: str,
        style: str,
        section_schema: Dict[str, Any],
        retrieved_clauses: List[Dict[str, Any]],
        extracted_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = PromptTemplates.TECHNICAL_SYNTHESIS_PROMPT.format(
            section_name=section_name,
            section_intent=section_intent,
            section_schema=json.dumps(section_schema),
            structured_context=json.dumps(extracted_context),
            retrieved_clauses=json.dumps(retrieved_clauses),
        ) + (
            f"\nStyle: {style}\nMandatory constraints: architecture_pattern must equal {extracted_context.get('architecture_pattern')}; "
            f"allowed_services={json.dumps(extracted_context.get('allowed_services', []))}; do not mention non-allowed services."
        )
        return self._run_writer_prompt(prompt=prompt, section_schema=section_schema, schema_name="technical_writer")

    @staticmethod
    def _generate_from_template(section_name: str, section_intent: str, section_schema: Dict[str, Any], intake: Dict[str, Any]) -> Dict[str, Any]:
        result = {k: v for k, v in section_schema.items()}
        if "content" in result:
            result["content"] = f"{section_name}: {section_intent}. Client={intake.get('client_name')} scope={intake.get('project_scope')}"
        return result

    def _run_writer_prompt(self, prompt: str, section_schema: Dict[str, Any], schema_name: str) -> Dict[str, Any]:
        response_format = self._json_schema_response_format(section_schema, schema_name)
        parsed = {}
        try:
            response = self.llm_service.generate_text_content(prompt=prompt, provider="generic", response_format=response_format)
            parsed = self._parse_json_object(response)
        except Exception as exc:
            logger.warning("Writer LLM failed: %s", exc)
        if not parsed:
            return {k: v for k, v in section_schema.items()}
        return {key: parsed.get(key, default) for key, default in section_schema.items()}

    @staticmethod
    def _json_schema_response_format(section_schema: Dict[str, Any], schema_name: str) -> Dict[str, Any]:
        properties = {}
        required = []
        for key, default in section_schema.items():
            required.append(key)
            if isinstance(default, list):
                properties[key] = {"type": "array", "items": {"type": "string"}}
            else:
                properties[key] = {"type": "string"}
        return {
            "type": "JSON_SCHEMA",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    def validate_section_output(self, section_def: SectionDefinition, section_json: Dict[str, Any], extracted_context: Dict[str, Any]) -> Tuple[bool, List[str]]:
        reasons = []

        for path in section_def.required_fields:
            if not self._json_path_non_empty(section_json, path):
                reasons.append(f"required field missing/empty: {path}")

        for field, rules in (section_def.min_content or {}).items():
            value = section_json.get(field)
            min_words = int((rules or {}).get("min_words", 0))
            min_items = int((rules or {}).get("min_items", 0))
            if min_words and isinstance(value, str) and len([w for w in value.split() if w.strip()]) < min_words:
                reasons.append(f"field '{field}' below min_words={min_words}")
            if min_items and isinstance(value, list) and len(value) < min_items:
                reasons.append(f"field '{field}' below min_items={min_items}")

        if section_def.category == "technical":
            expected_pattern = (extracted_context.get("architecture_pattern") or "").strip().lower()
            actual_pattern = (section_json.get("architecture_pattern") or "").strip().lower()
            if expected_pattern and actual_pattern and expected_pattern != actual_pattern:
                reasons.append("technical architecture_pattern mismatch with extractedContext")
            allowed = [s.lower() for s in extracted_context.get("allowed_services", [])]
            for service in self._extract_services_from_section(section_json):
                if allowed and service.lower() not in allowed:
                    reasons.append(f"service '{service}' is not in allowed_services")

        return len(reasons) == 0, reasons

    @staticmethod
    def _extract_services_from_section(section_json: Dict[str, Any]) -> List[str]:
        values = []
        for value in section_json.values():
            if isinstance(value, str):
                values.extend(re.findall(r"OCI [A-Za-z ]+|Object Storage|Data Science|AI Services", value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        values.extend(re.findall(r"OCI [A-Za-z ]+|Object Storage|Data Science|AI Services", item))
        return sorted(set(v.strip() for v in values if v.strip()))

    @staticmethod
    def _json_path_non_empty(doc: Dict[str, Any], path: str) -> bool:
        parts = path.split(".")
        current: Any = doc
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current.get(part)
        if current in (None, "", []):
            return False
        return True

    def _build_tbd_output(self, section_def: SectionDefinition) -> Dict[str, Any]:
        output = {}
        for key, default in section_def.output_schema.items():
            if isinstance(default, list):
                output[key] = ["TBD"]
            else:
                output[key] = "TBD"
        output["action_items"] = [
            "Refine clause filters and retry retrieval.",
            "Provide additional intake details for missing required fields.",
        ]
        return output

    @staticmethod
    def _resolve_writer_mode(category: str) -> str:
        if category == "technical":
            return "TECHNICAL_SYNTHESIS_MODE"
        if category == "template":
            return "TEMPLATE_MODE"
        return "CLAUSE_ASSEMBLY_MODE"

    def _render_section_markdown(self, section_name: str, category: str, content: Dict[str, Any]) -> str:
        if category == "technical":
            return self.render_architecture_markdown(content)
        return self._structured_section_to_markdown(section_name, content)

    @staticmethod
    def render_architecture_markdown(arch_json: Dict[str, Any]) -> str:
        lines = ["### FUTURE STATE ARCHITECTURE"]
        lines.append(arch_json.get("overview", ""))
        lines.append("")
        lines.append(f"**Architecture Pattern:** {arch_json.get('architecture_pattern', '')}")
        lines.append("**Core Components:**")
        for component in arch_json.get("core_components", []):
            lines.append(f"- {component}")
        lines.append(f"**Data Flow:** {arch_json.get('data_flow', '')}")
        lines.append(f"**Security Model:** {arch_json.get('security_model', '')}")
        lines.append(f"**Multi-Tenancy Model:** {arch_json.get('multi_tenancy_model', '')}")
        lines.append(f"**Limitations:** {arch_json.get('limitations', '')}")
        if arch_json.get("action_items"):
            lines.append("**Action Items:**")
            for item in arch_json.get("action_items", []):
                lines.append(f"- {item}")
        return "\n".join(lines).strip()

    @staticmethod
    def _diag(event: str, case_id: str, **kwargs: Any) -> None:
        logger.info("SOW_DIAG %s", json.dumps({"event": event, "case_id": case_id, **kwargs}, default=str))

    @staticmethod
    def _structured_section_to_markdown(section_name: str, content: Dict[str, Any]) -> str:
        lines = [f"### {section_name}"]
        for key, value in content.items():
            label = key.replace("_", " ").title()
            if isinstance(value, list):
                lines.append(f"**{label}:**")
                for item in value:
                    lines.append(f"- {item}")
            else:
                lines.append(f"**{label}:** {value}")
            lines.append("")
        return "\n".join(lines).strip()

    def _build_document_markdown(self, sow_case: SOWCase, structured_sections: List[Dict[str, Any]]) -> str:
        lines = [
            f"# Statement of Work - {sow_case.intake.get('client_name', 'Client')}",
            "",
            f"- **Project Scope:** {sow_case.intake.get('project_scope', 'N/A')}",
            f"- **Industry:** {sow_case.intake.get('industry', 'N/A')}",
            f"- **Region:** {sow_case.intake.get('region', 'N/A')}",
            f"- **Current Stage:** {sow_case.stage.value}",
            "",
            "---",
            "",
        ]
        for section in structured_sections:
            lines.append(f"## {section['name']}")
            lines.append("")
            lines.append(section.get("draft_markdown", ""))
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _section_definition_to_payload(section: SectionDefinition) -> Dict[str, Any]:
        return {
            "name": section.name,
            "intent": section.intent,
            "category": section.category,
            "clause_filters": section.clause_filters,
            "required_fields": section.required_fields,
            "min_content": section.min_content,
            "fallback_policy": {
                "min_clauses": section.fallback_policy.min_clauses,
                "relaxation_order": section.fallback_policy.relaxation_order,
                "max_retries": section.fallback_policy.max_retries,
            },
            "output_schema": section.output_schema,
        }

    @staticmethod
    def _payload_to_section_definition(payload: Dict[str, Any]) -> SectionDefinition:
        fallback = payload.get("fallback_policy") or {}
        return SectionDefinition(
            name=payload.get("name", ""),
            intent=payload.get("intent", ""),
            category=payload.get("category", "clause"),
            clause_filters=payload.get("clause_filters", {}),
            required_fields=payload.get("required_fields", []),
            min_content=payload.get("min_content", {}),
            fallback_policy=FallbackPolicy(
                min_clauses=int(fallback.get("min_clauses", 3)),
                relaxation_order=list(fallback.get("relaxation_order", ["tags", "industry", "region", "risk_level"])),
                max_retries=int(fallback.get("max_retries", 3)),
            ),
            output_schema=payload.get("output_schema", {}),
        )
