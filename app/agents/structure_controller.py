"""Deterministic SoW structure and section type routing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    "HIGH AVAILABILITY",
    "MANAGED SERVICES CONFIGURATION",
    "CLOSING FEEDBACK",
]
STATIC_SECTIONS = {
    "SOW VERSION HISTORY",        # always v1.0 boilerplate, no RAG value
    "COMPANY PROFILE",            # ✅ already static
    "SECURITY",                   # ✅ already static
    "HIGH AVAILABILITY",          # ✅ already static
    "MANAGED SERVICES CONFIGURATION",  # ✅ already static
    "CLOSING FEEDBACK",           # post-project human fill, no RAG value
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
        logger.warning("static_section.missing file=%s", file_name)
        return f"[{section} — to be completed]"

