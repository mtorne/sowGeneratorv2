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
    "CURRENT STATE ARCHITECTURE",
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
    # Optional subsections (e.g. BACKUP, DISASTER RECOVERY) can be added here
    # when in scope; add a matching static_section .md and Heading 4 in the
    # DOCX template via the patch script's SUBSECTIONS list.
    "IMPLEMENTATION DETAILS",
    "SECURITY",
    "HIGH AVAILABILITY",
    "MANAGED SERVICES CONFIGURATION",
    # ──────────────────────────────────────────────────────────────────────
    "CLOSING FEEDBACK",
]

STATIC_SECTIONS = {
    "SOW VERSION HISTORY",             # always v1.0 boilerplate, no RAG value
    "COMPANY PROFILE",                 # boilerplate; human-written per engagement
    "SECURITY",                        # standard OCI security boilerplate
    # Implementation Details subsections — scope-independent boilerplate
    "HIGH AVAILABILITY",               # standard HA design patterns
    "MANAGED SERVICES CONFIGURATION",  # standard OKE/managed services config
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
