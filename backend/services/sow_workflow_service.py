from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List
import json
import logging
import re
from uuid import uuid4

from services.knowledge_access_service import KnowledgeAccessService
from services.oci_client import OCIGenAIService

logger = logging.getLogger(__name__)
class WorkflowStage(str, Enum):
    INIT = "INIT"
    PLAN_READY = "PLAN_READY"
    RETRIEVED = "RETRIEVED"
    ASSEMBLED = "ASSEMBLED"
    DRAFTED = "DRAFTED"
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
class WorkflowError(Exception):
    pass
class StateTransitionError(WorkflowError):
    pass
class ValidationError(WorkflowError):
    pass
class SOWWorkflowService:
    """Deterministic orchestration service for PLAN->RETRIEVE->ASSEMBLE->WRITE->REVIEW."""
    STRUCTURED_ATTRIBUTES = [
        "deployment_model",
        "data_isolation",
        "cloud_provider",
        "ai_modes",
        "data_flow",
        "compliance_requirements",
    ]

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
        intake_with_context = dict(intake)
        intake_with_context["structured_context"] = self._extract_structured_context(intake)
        case_id = str(uuid4())
        sow_case = SOWCase(case_id=case_id, created_at=self._now_iso(), intake=intake_with_context)
        self._cases[case_id] = sow_case
        return sow_case
    def get_case(self, case_id: str) -> SOWCase:
        if case_id not in self._cases:
            raise ValidationError(f"Unknown case_id '{case_id}'")
        return self._cases[case_id]
    def run_plan(self, case_id: str, plan_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.INIT, WorkflowStage.PLAN_READY])
        sections = plan_input.get("sections") or []
        if not sections:
            raise ValidationError("PLAN requires non-empty sections")
        for section in sections:
            if not section.get("name") or not section.get("intent"):
                raise ValidationError("Each plan section requires name and intent")
        structured_context = sow_case.intake.get("structured_context") or self._extract_structured_context(sow_case.intake)
        retrieval_specs = []
        normalized_sections = []
        for section in sections:
            clause_filters = self.build_clause_filters(
                section_name=section["name"],
                clause_filters=section.get("clause_filters", {}),
                intake=sow_case.intake,
                structured_context=structured_context,
            )
            normalized_sections.append({**section, "clause_filters": clause_filters})
            retrieval_specs.append(
                {
                    "section": section["name"],
                    "clause_filters": clause_filters,
                }
            )
        artifact_payload = {
            "plan": {
                "sections": normalized_sections,
                "retrieval_specs": retrieval_specs,
                "structured_context": structured_context,
                "risk_checks": plan_input.get("risk_checks", []),
            }
        }
        artifact = self._append_artifact(sow_case, WorkflowStage.PLAN_READY, artifact_payload)
        sow_case.stage = WorkflowStage.PLAN_READY
        return artifact
    def run_retrieve(self, case_id: str, retrieve_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.PLAN_READY, WorkflowStage.RETRIEVED])
        latest_plan = self.get_latest_artifact(case_id, WorkflowStage.PLAN_READY)
        retrieval_specs = latest_plan.payload["plan"]["retrieval_specs"]
        section_names = retrieve_input.get("section_names") or []
        if section_names:
            selected = {str(name) for name in section_names}
            retrieval_specs = [spec for spec in retrieval_specs if spec.get("section") in selected]
            if not retrieval_specs:
                raise ValidationError("RETRIEVE received section_names but none matched plan sections")

        kb_results = retrieve_input.get("kb_results", {})
        allow_partial = bool(retrieve_input.get("allow_partial", False))
        top_k = max(1, int(retrieve_input.get("top_k", 5)))
        section_results = {}

        if allow_partial:
            logger.info(
                "RETRIEVE allow_partial enabled for case=%s. Running strict single-pass retrieval per section to avoid gateway timeouts.",
                case_id,
            )
        for spec in retrieval_specs:
            section_name = spec["section"]
            candidates = kb_results.get(section_name)
            if candidates is None:
                try:
                    candidates = self.knowledge_access_service.retrieve_section_clauses(
                        section_name=section_name,
                        filters=spec.get("clause_filters", {}),
                        intake=self._retrieval_context(sow_case.intake),
                        top_k=top_k,
                        allow_relaxed_retry=not allow_partial,
                    )
                except Exception as exc:
                    raise ValidationError(
                        f"RETRIEVE failed for section '{section_name}' from knowledge service: {str(exc)}"
                    ) from exc
            scoped = [c for c in candidates if c.get("metadata", {}).get("section") == section_name]
            if not scoped and candidates:
                # Safety fallback: if section tags drift despite retrieval being section-scoped,
                # treat retrieved candidates as scoped to this section.
                scoped = candidates
            valid = [
                c
                for c in scoped
                if c.get("chunk_id")
                and c.get("source_uri")
            ]
            for item in valid:
                item.setdefault("metadata", {})["section"] = section_name
            section_results[section_name] = valid[:top_k]
        if not all(section_results.values()) and not allow_partial:
            raise ValidationError(
                "RETRIEVE produced insufficient section coverage. "
                "Provide kb_results explicitly in payload for this run, limit section_names for batch retrieval, "
                "or set allow_partial=true when iterating through large plans."
            )

        if allow_partial:
            section_results = {k: v for k, v in section_results.items() if v}
            if not section_results:
                # Deterministic fallback: keep workflow progressing with explicit
                # placeholder evidence so users can continue to ASSEMBLE/WRITE and
                # see which sections still require real clause retrieval.
                section_results = self._build_placeholder_retrieval_set(
                    retrieval_specs=retrieval_specs,
                    intake=self._retrieval_context(sow_case.intake),
                )

        artifact = self._append_artifact(
            sow_case,
            WorkflowStage.RETRIEVED,
            {
                "retrieval_set": section_results,
                "meta": {
                    "requested_sections": [spec.get("section") for spec in retrieval_specs],
                    "allow_partial": allow_partial,
                    "returned_sections": list(section_results.keys()),
                },
            },
        )
        sow_case.stage = WorkflowStage.RETRIEVED
        return artifact

    @staticmethod
    def _build_placeholder_retrieval_set(retrieval_specs: List[Dict[str, Any]], intake: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        result: Dict[str, List[Dict[str, Any]]] = {}
        for spec in retrieval_specs:
            section_name = spec.get("section", "UNKNOWN_SECTION")
            filters = spec.get("clause_filters", {})
            result[section_name] = [
                {
                    "chunk_id": f"placeholder-{section_name.lower().replace(' ', '-').replace('/', '-')}",
                    "source_uri": "placeholder://needs-input",
                    "score": 0.01,
                    "clause_text": (
                        f"No knowledge-base clause returned for section '{section_name}'. "
                        f"Please refine retrieval filters and rerun. "
                        f"Current context industry={intake.get('industry')} region={intake.get('region')}."
                    ),
                    "metadata": {
                        "section": section_name,
                        "risk_level": filters.get("risk_level", ["medium"]),
                        "tags": filters.get("tags", []),
                    },
                }
            ]

        return result
    def run_assemble(self, case_id: str) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.RETRIEVED, WorkflowStage.ASSEMBLED])
        retrieval = self.get_latest_artifact(case_id, WorkflowStage.RETRIEVED).payload["retrieval_set"]
        plan_sections = self.get_latest_artifact(case_id, WorkflowStage.PLAN_READY).payload["plan"]["sections"]
        intent_by_section = {item.get("name"): item.get("intent", "") for item in plan_sections}
        schema_by_section = {item.get("name"): item.get("output_schema", {}) for item in plan_sections}
        blueprint = {}
        for section, clauses in retrieval.items():
            ordered = sorted(clauses, key=lambda c: c.get("score", 0), reverse=True)
            primary = [c["chunk_id"] for c in ordered[:2]]
            alternatives = [c["chunk_id"] for c in ordered[2:4]]
            blueprint[section] = {
                "section_intent": intent_by_section.get(section, ""),
                "order": [c["chunk_id"] for c in ordered],
                "primary_clause_ids": primary,
                "primary_clauses": ordered[:2],
                "alternatives": alternatives,
                "output_schema": schema_by_section.get(section, {}),
                "conflicts": [],
            }
        artifact = self._append_artifact(
            sow_case,
            WorkflowStage.ASSEMBLED,
            {"assembly_blueprint": blueprint},
        )
        sow_case.stage = WorkflowStage.ASSEMBLED
        return artifact
    def run_write(self, case_id: str, write_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.ASSEMBLED, WorkflowStage.DRAFTED])
        blueprint = self.get_latest_artifact(case_id, WorkflowStage.ASSEMBLED).payload["assembly_blueprint"]
        plan = self.get_latest_artifact(case_id, WorkflowStage.PLAN_READY).payload["plan"]
        style = write_input.get("style", "professional")
        prohibited = write_input.get("prohibited_commitments", [])
        structured_sections = []
        section_order = [item.get("name") for item in plan.get("sections", [])]
        for section in section_order:
            config = blueprint.get(section, {})
            clause_ids = config.get("primary_clause_ids", [])
            section_intent = config.get("section_intent") or "Define terms and obligations for this section"
            evidence_lines = []
            for clause in config.get("primary_clauses", []):
                raw_text = (clause.get("clause_text") or "").strip()
                if raw_text:
                    evidence_lines.append(raw_text)

            evidence_lines = evidence_lines[:2]

            section_schema = config.get("output_schema") or {
                "core_architecture": "",
                "security_controls": [],
                "data_flow_description": "",
                "limitations": [],
            }
            structured_output = self._write_structured_section(
                section_name=section,
                section_intent=section_intent,
                style=style,
                section_schema=section_schema,
                retrieved_clauses=config.get("primary_clauses", []),
                intake=sow_case.intake,
            )
            text = self._structured_section_to_markdown(section, structured_output)
            if any(word.lower() in text.lower() for word in prohibited):
                raise ValidationError(f"WRITE produced prohibited commitment language in section '{section}'")
            structured_sections.append(
                {
                    "name": section,
                    "intent": section_intent,
                    "structured_content": structured_output,
                    "draft_markdown": text,
                    "source_mapping": [{"paragraph": 1, "clause_ids": clause_ids}],
                }
            )

        markdown = self._build_document_markdown(sow_case, structured_sections)
        artifact = self._append_artifact(
            sow_case,
            WorkflowStage.DRAFTED,
            {"draft": {"structured_sections": structured_sections, "markdown": markdown}},
        )
        sow_case.stage = WorkflowStage.DRAFTED
        return artifact
    def run_review(self, case_id: str, review_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.DRAFTED, WorkflowStage.REVIEWED])
        draft = self.get_latest_artifact(case_id, WorkflowStage.DRAFTED).payload["draft"]["structured_sections"]
        findings = []
        forbidden_phrases = review_input.get("forbidden_phrases", ["guarantee", "without exception"])
        for section_data in draft:
            section_name = section_data["name"]
            text = section_data["draft_markdown"].lower()
            for phrase in forbidden_phrases:
                if phrase.lower() in text:
                    findings.append(
                        {
                            "severity": "critical",
                            "type": "risk",
                            "section": section_name,
                            "evidence": phrase,
                            "recommendation": "Replace absolute commitments with bounded language",
                        }
                    )
            if not section_data.get("source_mapping"):
                findings.append(
                    {
                        "severity": "critical",
                        "type": "grounding",
                        "section": section_name,
                        "evidence": "missing source mapping",
                        "recommendation": "Add clause mapping for each paragraph",
                    }
                )
        status = "pass" if not any(f["severity"] == "critical" for f in findings) else "fail"
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
        if sow_case.stage not in [WorkflowStage.DRAFTED, WorkflowStage.REVIEWED, WorkflowStage.APPROVED]:
            raise ValidationError("Document is available only after WRITE stage")
        draft = self.get_latest_artifact(case_id, WorkflowStage.DRAFTED).payload["draft"]
        return draft.get("markdown", "")
    def render_document_html(self, case_id: str) -> str:
        markdown = self.render_document_markdown(case_id)
        # Simple deterministic markdown-to-html conversion for headings/paragraphs.
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
        artifact = WorkflowArtifact(
            stage=stage,
            version=len(artifact_list) + 1,
            created_at=self._now_iso(),
            payload=payload,
        )
        artifact_list.append(artifact)
        return artifact
    @staticmethod
    def _ensure_stage(sow_case: SOWCase, allowed_stages: List[WorkflowStage]) -> None:
        if sow_case.stage not in allowed_stages:
            allowed = ", ".join(stage.value for stage in allowed_stages)
            raise StateTransitionError(
                f"Invalid stage transition from {sow_case.stage.value}. Expected one of: {allowed}"
            )
    @staticmethod
    def _validate_intake(intake: Dict[str, Any]) -> None:
        required = ["client_name", "project_scope", "document_type", "industry", "region"]
        missing = [field for field in required if not intake.get(field)]
        if missing:
            raise ValidationError(f"Missing intake fields: {', '.join(missing)}")

    def _extract_structured_context(self, intake: Dict[str, Any]) -> Dict[str, Any]:
        prompt = (
            "Extract structured delivery attributes from intake data for SoW retrieval. "
            "Return strict JSON with keys deployment_model, data_isolation, cloud_provider, ai_modes, data_flow, compliance_requirements. "
            "ai_modes and compliance_requirements must be arrays. Use null or empty arrays when unknown. "
            f"Intake JSON: {json.dumps(intake)}"
        )
        try:
            response = self.llm_service.generate_text_content(prompt=prompt, provider="generic")
            parsed = self._parse_json_object(response)
        except Exception as exc:
            logger.warning("Structured context extraction failed, using fallback defaults: %s", exc)
            parsed = {}
        normalized = {
            "deployment_model": parsed.get("deployment_model"),
            "data_isolation": parsed.get("data_isolation"),
            "cloud_provider": parsed.get("cloud_provider"),
            "ai_modes": parsed.get("ai_modes") or [],
            "data_flow": parsed.get("data_flow"),
            "compliance_requirements": parsed.get("compliance_requirements") or [],
        }
        return normalized

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

    def _retrieval_context(self, intake: Dict[str, Any]) -> Dict[str, Any]:
        context = {
            "industry": intake.get("industry"),
            "region": intake.get("region"),
            "document_type": intake.get("document_type"),
        }
        context.update(intake.get("structured_context") or {})
        return context

    def build_clause_filters(
        self,
        section_name: str,
        clause_filters: Dict[str, Any],
        intake: Dict[str, Any],
        structured_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        resolved = {"section": section_name, **(clause_filters or {})}
        for key, value in list(resolved.items()):
            if isinstance(value, str):
                resolved[key] = self._resolve_template_value(value, intake, structured_context)
        for key in ["industry", "region", *self.STRUCTURED_ATTRIBUTES]:
            if key not in resolved or resolved[key] in (None, ""):
                if key in structured_context:
                    resolved[key] = structured_context.get(key)
                else:
                    resolved[key] = intake.get(key)
        return resolved

    @staticmethod
    def _resolve_template_value(value: str, intake: Dict[str, Any], structured_context: Dict[str, Any]) -> Any:
        template_match = re.fullmatch(r"\{\{\s*intake\.([a-zA-Z0-9_]+)\s*\}\}", value)
        if template_match:
            key = template_match.group(1)
            return intake.get(key)
        context_match = re.fullmatch(r"\{\{\s*structured\.([a-zA-Z0-9_]+)\s*\}\}", value)
        if context_match:
            key = context_match.group(1)
            return structured_context.get(key)
        return value

    def _write_structured_section(
        self,
        section_name: str,
        section_intent: str,
        style: str,
        section_schema: Dict[str, Any],
        retrieved_clauses: List[Dict[str, Any]],
        intake: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = (
            "You are writing one Statement of Work section.\n"
            "You must use only the provided clauses as building blocks.\n"
            "Do not invent new obligations or services.\n"
            "Do not use sources outside the provided retrieval results.\n"
            "Do not contradict intake constraints.\n"
            "Rephrase for coherence while preserving meaning.\n"
            "Return STRICT JSON only matching the provided section_schema keys.\n"
            f"Section Name: {section_name}\n"
            f"Section Intent: {section_intent}\n"
            f"Style: {style}\n"
            f"Intake Constraints: {json.dumps(self._retrieval_context(intake))}\n"
            f"Section Schema: {json.dumps(section_schema)}\n"
            f"Retrieved Clauses: {json.dumps(retrieved_clauses)}"
        )
        parsed = {}
        try:
            response = self.llm_service.generate_text_content(prompt=prompt, provider="generic")
            parsed = self._parse_json_object(response)
        except Exception as exc:
            logger.warning("Writer LLM failed for section=%s: %s", section_name, exc)
        if not parsed:
            parsed = {k: section_schema.get(k) for k in section_schema.keys()}
            parsed["limitations"] = parsed.get("limitations") or [
                "Unable to draft from clauses; rerun WRITE with richer retrieval evidence."
            ]
        return {key: parsed.get(key, default) for key, default in section_schema.items()}

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
