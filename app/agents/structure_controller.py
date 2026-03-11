"""Deterministic SoW structure and section type routing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Flat canonical order used for generation and DocBuilder injection.
# Sections marked STATIC in STATIC_SECTIONS are injected verbatim from
# app/templates/static_sections/<slug>.md.  All other sections are written
# by WriterAgent using RAG context + architecture vision analysis.
CANONICAL_STRUCTURE = [
    "SOW VERSION HISTORY",
    "STATUS AND NEXT STEPS",
    "PROJECT PARTICIPANTS",
    "COMPANY PROFILE",
    "IN SCOPE APPLICATION",
    "PROJECT OVERVIEW",
    # ── Scope ─────────────────────────────────────────────────────────────────
    # Customer-focused deliverables proposal.  Injected into the "Scope" H3
    # sub-heading inside Project Overview.  LLM writes factual deliverables
    # with no Oracle/joint language.
    "SCOPE",
    "CURRENT STATE ARCHITECTURE",
    # ── Current State sub-section ─────────────────────────────────────────────
    # H3 "Current State Architecture - Description".  Detailed component-level
    # description written by the LLM from architecture_analysis.current data.
    # The H1 "Current State Architecture" keeps its template intro text only.
    "CURRENT STATE ARCHITECTURE DESCRIPTION",
    "CURRENTLY USED TECHNOLOGY STACK",
    "OCI SERVICE SIZING AND AMOUNTS",
    "FUTURE STATE ARCHITECTURE",
    "ARCHITECTURE DEPLOYMENT OVERVIEW",
    # ── Future State sub-sections ──────────────────────────────────────────
    # "Architecture Components" is a Heading 3 inside Future State Architecture
    # that should enumerate project-specific components grouped by category
    # (Networking, Compute, Storage, Security, DevOps & Management).
    "ARCHITECTURE COMPONENTS",
    # ── Implementation Details ─────────────────────────────────────────────
    # "Implementation Details and Configuration Settings" in the DOCX template
    # is the Heading 3 parent. The LLM writes the introductory body content;
    # HA and MC are static Heading 4 subsections patched in by
    # scripts/patch_template_headings.py.
    "IMPLEMENTATION DETAILS",
    # ── Milestone Plan ──────────────────────────────────────────────────────
    # LLM-generated project phase plan derived from the gap between current
    # and target architecture.  Placed after implementation details so the
    # reader sees the delivery roadmap immediately after the technical spec.
    "MILESTONE PLAN",
    "SECURITY",
    # ── Operative sections — LLM-generated from diagram analysis ──────────
    # HIGH AVAILABILITY, BACKUP STRATEGY, and DISASTER RECOVERY are all
    # written by the LLM using the architecture_analysis context so the
    # content reflects the actual components identified in the diagrams.
    "HIGH AVAILABILITY",
    "BACKUP STRATEGY",
    "DISASTER RECOVERY",
    "MANAGED SERVICES CONFIGURATION",
    # ── Architect Review ───────────────────────────────────────────────────
    # Final section providing generation quality feedback, unknown data gaps,
    # next steps to complete the SoW, and recommendations.  This section is
    # NOT in the DOCX template — it is appended at the end of the document
    # via _append_section so the architect has a structured audit trail of
    # what was auto-generated and what still needs manual attention.
    "ARCHITECT REVIEW",
    # ──────────────────────────────────────────────────────────────────────
    "CLOSING FEEDBACK",
]

STATIC_SECTIONS = {
    "SOW VERSION HISTORY",             # always v1.0 boilerplate, no RAG value
    "COMPANY PROFILE",                 # boilerplate; human-written per engagement
    # CURRENT STATE ARCHITECTURE is intentionally static with an empty file so
    # the template H1-level intro text is preserved as-is.  Detailed description
    # is handled by the separate CURRENT STATE ARCHITECTURE DESCRIPTION section.
    "CURRENT STATE ARCHITECTURE",
    # SECURITY is now LLM-generated (architecture-aware) — removed from static.
    # MANAGED SERVICES CONFIGURATION is now LLM-generated — removed from static.
    "CLOSING FEEDBACK",                # post-project human fill, no RAG value
}


@dataclass(frozen=True)
class StructureController:
    """Deterministic controller for fixed SoW section order."""

    template_root: Path

    def sections(self) -> list[str]:
        """Return canonical section order."""
        return CANONICAL_STRUCTURE.copy()

    def is_static(self, section: str) -> bool:
        """Return whether section should be injected from static templates."""
        return section in STATIC_SECTIONS

    def inject_template(self, section: str) -> str:
        """Load versioned static section content from templates directory."""
        file_name = section.lower().replace(" ", "_") + ".md"
        template_path = self.template_root / "static_sections" / file_name
        if template_path.exists():
            return template_path.read_text(encoding="utf-8").strip()

        # Safe named fallback instead of disclaimer+generic_oci
        return f"[{section} — to be completed]"
