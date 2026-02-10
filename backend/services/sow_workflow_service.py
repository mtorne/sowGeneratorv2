from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List
from uuid import uuid4

from services.knowledge_access_service import KnowledgeAccessService
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
    def __init__(self, knowledge_access_service: KnowledgeAccessService | None = None) -> None:
        self._cases: Dict[str, SOWCase] = {}
        self.knowledge_access_service = knowledge_access_service or KnowledgeAccessService()
    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
    def create_case(self, intake: Dict[str, Any]) -> SOWCase:
        self._validate_intake(intake)
        case_id = str(uuid4())
        sow_case = SOWCase(case_id=case_id, created_at=self._now_iso(), intake=intake)
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
        retrieval_specs = []
        for section in sections:
            retrieval_specs.append(
                {
                    "section": section["name"],
                    "filters": {
                        "section": section["name"],
                        "clause_type": section.get("clause_type", "general"),
                        "risk_level": section.get("max_risk", "medium"),
                        "industry": plan_input.get("industry", "general"),
                        "region": plan_input.get("region", "global"),
                    },
                }
            )
        artifact_payload = {
            "plan": {
                "sections": sections,
                "retrieval_specs": retrieval_specs,
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
        section_results = {}
        for spec in retrieval_specs:
            section_name = spec["section"]
            candidates = kb_results.get(section_name)
            if candidates is None:
                try:
                    candidates = self.knowledge_access_service.retrieve_section_clauses(
                        section_name=section_name,
                        filters=spec.get("filters", {}),
                        intake=sow_case.intake,
                        top_k=retrieve_input.get("top_k", 5),
                    )
                except Exception as exc:
                    raise ValidationError(
                        f"RETRIEVE failed for section '{section_name}' from knowledge service: {str(exc)}"
                    ) from exc
            scoped = [c for c in candidates if c.get("metadata", {}).get("section") == section_name]
            valid = [
                c
                for c in scoped
                if c.get("metadata", {}).get("clause_type")
                and c.get("metadata", {}).get("risk_level")
                and c.get("chunk_id")
                and c.get("source_uri")
            ]
            section_results[section_name] = valid[: retrieve_input.get("top_k", 5)]
        if not all(section_results.values()) and not allow_partial:
            raise ValidationError(
                "RETRIEVE produced insufficient section coverage. "
                "Provide kb_results explicitly in payload for this run, limit section_names for batch retrieval, "
                "or set allow_partial=true when iterating through large plans."
            )

        if allow_partial:
            section_results = {k: v for k, v in section_results.items() if v}
            if not section_results:
                raise ValidationError("RETRIEVE allow_partial=true but no sections returned candidates")

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
    def run_assemble(self, case_id: str) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.RETRIEVED, WorkflowStage.ASSEMBLED])
        retrieval = self.get_latest_artifact(case_id, WorkflowStage.RETRIEVED).payload["retrieval_set"]
        plan_sections = self.get_latest_artifact(case_id, WorkflowStage.PLAN_READY).payload["plan"]["sections"]
        intent_by_section = {item.get("name"): item.get("intent", "") for item in plan_sections}
        blueprint = {}
        for section, clauses in retrieval.items():
            ordered = sorted(clauses, key=lambda c: c.get("score", 0), reverse=True)
            primary = [c["chunk_id"] for c in ordered[:2]]
            alternatives = [c["chunk_id"] for c in ordered[2:4]]
            blueprint[section] = {
                "section_intent": intent_by_section.get(section, ""),
                "order": [c["chunk_id"] for c in ordered],
                "primary_clause_ids": primary,
                "alternatives": alternatives,
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
        style = write_input.get("style", "professional")
        prohibited = write_input.get("prohibited_commitments", [])
        sections = {}
        for section, config in blueprint.items():
            clause_ids = config["primary_clause_ids"]
            clause_id_text = [str(clause_id) for clause_id in clause_ids]
            section_intent = config.get("section_intent") or "Define terms and obligations for this section"
            text = (
                f"{section}: This section is drafted in {style} tone and is intended to {section_intent.lower()}. "
                f"The draft is grounded in approved clauses {', '.join(clause_id_text)} and translates them into client-specific obligations, "
                f"scope boundaries, and delivery expectations. Assumptions and constraints should be validated during review before approval."
            )
            if any(word.lower() in text.lower() for word in prohibited):
                raise ValidationError(f"WRITE produced prohibited commitment language in section '{section}'")
            sections[section] = {
                "draft_text": text,
                "source_mapping": [{"paragraph": 1, "clause_ids": clause_ids}],
            }
        artifact = self._append_artifact(
            sow_case,
            WorkflowStage.DRAFTED,
            {"draft": {"sections": sections}},
        )
        sow_case.stage = WorkflowStage.DRAFTED
        return artifact
    def run_review(self, case_id: str, review_input: Dict[str, Any]) -> WorkflowArtifact:
        sow_case = self.get_case(case_id)
        self._ensure_stage(sow_case, [WorkflowStage.DRAFTED, WorkflowStage.REVIEWED])
        draft = self.get_latest_artifact(case_id, WorkflowStage.DRAFTED).payload["draft"]["sections"]
        findings = []
        forbidden_phrases = review_input.get("forbidden_phrases", ["guarantee", "without exception"])
        for section_name, section_data in draft.items():
            text = section_data["draft_text"].lower()
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
        draft_sections = self.get_latest_artifact(case_id, WorkflowStage.DRAFTED).payload["draft"]["sections"]
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
        for section_name, section_data in draft_sections.items():
            lines.append(f"## {section_name}")
            lines.append("")
            lines.append(section_data.get("draft_text", ""))
            lines.append("")
        return "\n".join(lines)
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
