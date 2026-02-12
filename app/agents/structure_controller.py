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
    "COMPANY PROFILE",
    "SECURITY",
    "HIGH AVAILABILITY",
    "MANAGED SERVICES CONFIGURATION",
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

        disclaimer = (self.template_root / "static_sections" / "disclaimer.md").read_text(encoding="utf-8").strip()
        generic_oci = (self.template_root / "static_sections" / "generic_oci_explanations.md").read_text(encoding="utf-8").strip()
        return f"{disclaimer}\n\n{generic_oci}"
