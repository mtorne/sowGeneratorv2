"""Document assembly services."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from uuid import uuid4

from docx import Document
from docx.oxml import OxmlElement

logger = logging.getLogger(__name__)

# Maps canonical section names → keyword to match in template heading text
SECTION_HEADING_KEYWORDS: dict[str, str] = {
    "SOW VERSION HISTORY":              "version history",
    "STATUS AND NEXT STEPS":            "status and next",
    "PROJECT PARTICIPANTS":             "project participants",
    "COMPANY PROFILE":                  "company profile",
    "IN SCOPE APPLICATION":            "in scope application",
    "PROJECT OVERVIEW":                 "project overview",
    "CURRENT STATE ARCHITECTURE":       "current state architecture",
    "CURRENTLY USED TECHNOLOGY STACK":  "currently used technology",
    "OCI SERVICE SIZING AND AMOUNTS":   "oci service sizing",
    "FUTURE STATE ARCHITECTURE":        "future state architecture",
    "ARCHITECTURE DEPLOYMENT OVERVIEW": "architecture deployment",
    "SECURITY":                         "security",
    "HIGH AVAILABILITY":                "high availability",
    "MANAGED SERVICES CONFIGURATION":   "managed services",
    "CLOSING FEEDBACK":                 "closing feedback",
}

# Placeholder text patterns to remove when found inside a section
_PLACEHOLDER_RE = re.compile(
    r"<add information here>"
    r"|<Information to be filled in[^>]*>"
    r"|\[Add description here\]"
    r"|\[add information here\]",
    re.IGNORECASE,
)


class DocumentBuilder:
    """Injects generated text into a DOCX template."""

    def __init__(self, template_path: Path) -> None:
        self.template_path = template_path

    def _load_or_create_template(self) -> Document:
        if self.template_path.exists():
            return Document(str(self.template_path))
        logger.warning("Template not found at %s. Using generated fallback template", self.template_path)
        return Document()

    def build(self, sections: list[tuple[str, str]], output_dir: Path) -> str:
        """Inject sections into template headings and save DOCX."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"output_{uuid4().hex}.docx"
        output_path = output_dir / output_name

        doc = self._load_or_create_template()

        if self.template_path.exists():
            for section_name, content in sections:
                if not content or not content.strip():
                    continue
                injected = self._inject_section(doc, section_name, content)
                if not injected:
                    logger.warning("doc_builder.section_not_found section=%s — appending at end", section_name)
                    self._append_section(doc, section_name, content)
        else:
            # Fallback: build from scratch
            for section_name, content in sections:
                doc.add_heading(section_name.title(), level=1)
                for block in content.split("\n\n"):
                    if block.strip():
                        doc.add_paragraph(block.strip())

        doc.save(str(output_path))
        logger.info("Saved generated SoW document: %s", output_path)
        return output_name

    def build_markdown(self, full_document: str, output_dir: Path) -> str:
        """Save generated content as markdown and return file name."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"output_{uuid4().hex}.md"
        output_path = output_dir / output_name
        output_path.write_text(full_document, encoding="utf-8")
        logger.info("Saved generated SoW markdown: %s", output_path)
        return output_name

    def _inject_section(self, doc: Document, section_name: str, content: str) -> bool:
        """Find heading in template, remove placeholder paragraphs, inject content."""
        keyword = SECTION_HEADING_KEYWORDS.get(section_name.upper(), section_name.lower())
        paragraphs = list(doc.paragraphs)

        # Find the matching heading paragraph
        heading_idx: int | None = None
        heading_level = 1
        for i, para in enumerate(paragraphs):
            if not para.style.name.startswith("Heading"):
                continue
            if keyword.lower() in para.text.lower():
                heading_idx = i
                heading_level = self._get_heading_level(para) or 1
                break

        if heading_idx is None:
            return False

        # Find range: heading+1 → next same-or-higher-level heading
        next_heading_idx = len(paragraphs)
        for i in range(heading_idx + 1, len(paragraphs)):
            lvl = self._get_heading_level(paragraphs[i])
            if lvl is not None and lvl <= heading_level:
                next_heading_idx = i
                break

        body = doc.element.body

        # Remove only placeholder paragraphs — preserve sub-headings and tables
        for i in range(heading_idx + 1, next_heading_idx):
            para = paragraphs[i]
            if _PLACEHOLDER_RE.search(para.text):
                body.remove(para._element)

        # Insert content blocks directly after the heading element
        heading_elem = paragraphs[heading_idx]._element
        content_blocks = [b.strip() for b in content.split("\n\n") if b.strip()]

        for block in reversed(content_blocks):
            new_para = OxmlElement("w:p")
            new_run = OxmlElement("w:r")
            new_text = OxmlElement("w:t")
            new_text.text = block
            new_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            new_run.append(new_text)
            new_para.append(new_run)
            heading_elem.addnext(new_para)

        logger.info("doc_builder.section_injected section=%s blocks=%d", section_name, len(content_blocks))
        return True

    def _append_section(self, doc: Document, section_name: str, content: str) -> None:
        """Fallback: append section at end of document."""
        doc.add_heading(section_name.title(), level=1)
        for block in content.split("\n\n"):
            if block.strip():
                doc.add_paragraph(block.strip())

    @staticmethod
    def _get_heading_level(para) -> int | None:
        style_name = para.style.name
        if style_name.startswith("Heading"):
            try:
                return int(style_name.split()[-1])
            except (ValueError, IndexError):
                return 1
        return None
