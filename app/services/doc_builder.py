"""DOCX document assembly service."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from docx import Document

logger = logging.getLogger(__name__)


class DocumentBuilder:
    """Injects generated text into a DOCX template."""

    def __init__(self, template_path: Path) -> None:
        """Initialize builder with a template path."""
        self.template_path = template_path

    def _load_or_create_template(self) -> Document:
        """Load template from disk or create an in-memory default template."""
        if self.template_path.exists():
            return Document(str(self.template_path))
        logger.warning("Template not found at %s. Using generated fallback template", self.template_path)
        doc = Document()
        doc.add_paragraph("{{FULL_DOCUMENT}}")
        return doc

    def build(self, full_document: str, output_dir: Path) -> str:
        """Render template and save output file name."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"output_{uuid4().hex}.docx"
        output_path = output_dir / output_name

        doc = self._load_or_create_template()
        replaced = False
        for paragraph in doc.paragraphs:
            if "{{FULL_DOCUMENT}}" in paragraph.text:
                paragraph.text = paragraph.text.replace("{{FULL_DOCUMENT}}", full_document)
                replaced = True

        if not replaced:
            doc.add_paragraph(full_document)

        doc.save(str(output_path))
        logger.info("Saved generated SoW document: %s", output_path)
        return output_name
