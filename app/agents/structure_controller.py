"""Deterministic SoW structure and section type routing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Flat canonical order used for generation and DocBuilder injection.
# "Implementation Details and Configuration Settings" in the DOCX template is
# the Heading 1 parent; the sections below it are injected as Heading 2
# subsections (see scripts/patch_template_headings.py).
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
    "SECURITY",
    # ── Implementation Details subsections ───────────────────────────────────
    # These map to Heading 2 paragraphs inside the "Implementation Details and
    # Configuration Settings" Heading 1 in the DOCX template.
    # Optional subsections (e.g. BACKUP, DISASTER RECOVERY) can be added here
    # when they are in scope; add a matching static template file in
    # app/templates/static_sections/ and a Heading 2 entry in the DOCX template
    # via scripts/patch_template_headings.py (SUBSECTIONS list).
    "HIGH AVAILABILITY",
    "MANAGED SERVICES CONFIGURATION",
    # ─────────────────────────────────────────────────────────────────────────
    "CLOSING FEEDBACK",
]

STATIC_SECTIONS = {
    "SOW VERSION HISTORY",             # always v1.0 boilerplate, no RAG value
    "COMPANY PROFILE",                 # ✅ already static
    "SECURITY",                        # ✅ already static
    # Implementation Details subsections — boilerplate text, scope-independent
    "HIGH AVAILABILITY",               # ✅ already static
    "MANAGED SERVICES CONFIGURATION",  # ✅ already static
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
