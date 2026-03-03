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
    "SOW VERSION HISTORY":                          "version history",
    "STATUS AND NEXT STEPS":                        "status and next",
    "PROJECT PARTICIPANTS":                         "project participants",
    "COMPANY PROFILE":                              "company profile",
    "IN SCOPE APPLICATION":                         "in scope application",
    "PROJECT OVERVIEW":                             "project overview",
    "CURRENT STATE ARCHITECTURE":                   "current state architecture",
    "CURRENTLY USED TECHNOLOGY STACK":              "currently used technology",
    "OCI SERVICE SIZING AND AMOUNTS":               "oci service sizing",
    "FUTURE STATE ARCHITECTURE":                    "future state architecture",
    "ARCHITECTURE DEPLOYMENT OVERVIEW":             "architecture deployment",
    "ARCHITECTURE COMPONENTS":                      "architecture components",
    "IMPLEMENTATION DETAILS":                       "implementation details",
    "SECURITY":                                     "security",
    "HIGH AVAILABILITY":                            "high availability",
    "MANAGED SERVICES CONFIGURATION":               "managed services",
    "CLOSING FEEDBACK":                             "closing feedback",
}

# Placeholder text patterns to remove when found inside a section
_PLACEHOLDER_RE = re.compile(
    r"<add information here>"
    r"|<Information to be filled in[^>]*>"
    r"|<Diagram to be provided[^>]*>"
    r"|<Elaborate on the architecture[^>]*>"
    r"|<Enumerate the target[^>]*>"
    r"|<Final thoughts[^>]*>"
    r"|\[Add description here\]"
    r"|\[add information here\]"
    r"|Relevant aspects of the architecture, how to deploy.*$",  # Implementation Details placeholder
    re.IGNORECASE,
)

# Known prefixes in template runs that are immediately followed by a blank
# customer-name run.  Ordered longest-match first to avoid short-prefix false
# positives.
_CUSTOMER_PREFIX_SUFFIXES = (
    "the Non-Disclosure Agreement between ",
    "free of charge for the ",
    "to enable ",
    "available to ",
    "Oracle-",
    "initial format by ",
    "granted for ",
    "the  Oracle",          # "the [customer] Oracle Labs" — blank INSIDE run
    "under the  Oracle",
    "based on the ",
    "under ",
    "The ",
    "fall under ",
    "ACE/Sales and ",
    "for the ",
    "provided by ",
)

# Known literal placeholder tokens to replace globally
_LITERAL_SUBSTITUTIONS: list[tuple[str, str]] = [
    ("Customer1", "{customer_name}"),
    ("Project1",  "{project_name}"),
]


class DocumentBuilder:
    """Injects generated text into a DOCX template."""

    def __init__(
        self,
        template_path: Path,
        customer_name: str = "",
        project_name: str = "",
    ) -> None:
        self.template_path = template_path
        self.customer_name = customer_name.strip()
        self.project_name = project_name.strip()

    def _load_or_create_template(self) -> Document:
        if self.template_path.exists():
            return Document(str(self.template_path))
        logger.warning("Template not found at %s. Using generated fallback template", self.template_path)
        return Document()

    # ------------------------------------------------------------------
    # Customer / project name substitution
    # ------------------------------------------------------------------

    def _substitute_names(self, doc: Document) -> None:
        """Replace 'Customer1' / 'Project1' placeholders with actual names.

        Two passes:
        1. Replace literal placeholder tokens in every run.
        2. Fill blank/space-only runs that sit between context runs where
           the customer name belongs (identified by the preceding run's suffix).
        """
        if not self.customer_name and not self.project_name:
            return

        token_map: dict[str, str] = {}
        if self.customer_name:
            token_map["Customer1"] = self.customer_name
        if self.project_name:
            token_map["Project1"] = self.project_name

        def _process_para_runs(para) -> None:
            # Pass 1: literal token replacement
            for run in para.runs:
                for token, replacement in token_map.items():
                    if token in run.text:
                        run.text = run.text.replace(token, replacement)

            # Pass 2: fill blank runs that carry the customer-name placeholder.
            # A blank run is one whose text is '' or purely whitespace.
            if not self.customer_name:
                return
            runs = para.runs
            for i, run in enumerate(runs):
                if run.text.strip() != "":
                    continue  # not a blank run
                if i == 0:
                    continue
                prev_text = runs[i - 1].text
                for suffix in _CUSTOMER_PREFIX_SUFFIXES:
                    if prev_text.endswith(suffix):
                        run.text = self.customer_name
                        logger.debug(
                            "doc_builder.customer_name_injected run_idx=%d prev_suffix=%r",
                            i,
                            suffix,
                        )
                        break

        # Body paragraphs
        for para in doc.paragraphs:
            _process_para_runs(para)
        # Table cells
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        _process_para_runs(para)

        logger.info(
            "doc_builder.names_substituted customer=%r project=%r",
            self.customer_name,
            self.project_name,
        )

    def build(self, sections: list[tuple[str, str]], output_dir: Path) -> str:
        """Inject sections into template headings and save DOCX."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"output_{uuid4().hex}.docx"
        output_path = output_dir / output_name

        doc = self._load_or_create_template()
        self._substitute_names(doc)

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

        # Find the matching heading paragraph (body-level paragraphs only)
        heading_idx: int | None = None
        heading_level = 1
        for i, para in enumerate(paragraphs):
            if not self._is_heading_style(para):
                continue
            if keyword.lower() in para.text.lower():
                heading_idx = i
                heading_level = self._get_heading_level(para) or 1
                break

        if heading_idx is None:
            # Fallback: search inside table cells — python-docx excludes these from doc.paragraphs
            table_elem = self._find_heading_in_tables(doc, keyword)
            if table_elem is not None:
                logger.info(
                    "doc_builder.heading_in_table section=%s — injecting after table element",
                    section_name,
                )
                self._inject_blocks_after_element(table_elem, content)
                content_blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
                logger.info(
                    "doc_builder.section_injected section=%s blocks=%d via table_fallback",
                    section_name,
                    len(content_blocks),
                )
                return True

            available_headings = [p.text for p in paragraphs if self._is_heading_style(p)]
            logger.warning(
                "doc_builder.heading_not_found section=%s keyword=%r — "
                "available template headings: %s",
                section_name,
                keyword,
                available_headings,
            )
            return False

        # Find range: heading+1 → next same-or-higher-level heading
        next_heading_idx = len(paragraphs)
        for i in range(heading_idx + 1, len(paragraphs)):
            lvl = self._get_heading_level(paragraphs[i])
            if lvl is not None and lvl <= heading_level:
                next_heading_idx = i
                break

        body = doc.element.body

        # Remove placeholder paragraphs (but not sub-headings or real content)
        for i in range(heading_idx + 1, next_heading_idx):
            para = paragraphs[i]
            if _PLACEHOLDER_RE.search(para.text):
                body.remove(para._element)

        # Determine the injection anchor: prefer inserting *after* the first
        # real non-placeholder, non-sub-heading paragraph in the section
        # (i.e., after the template's intro sentence) so the LLM content
        # follows naturally after it rather than being prepended before it.
        anchor_elem = paragraphs[heading_idx]._element
        for i in range(heading_idx + 1, next_heading_idx):
            para = paragraphs[i]
            lvl = self._get_heading_level(para)
            if lvl is not None:
                break  # hit a sub-heading — keep current anchor
            if para.text.strip() and not _PLACEHOLDER_RE.search(para.text):
                anchor_elem = para._element  # advance anchor past intro para(s)

        self._inject_blocks_after_element(anchor_elem, content)
        content_blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
        logger.info("doc_builder.section_injected section=%s blocks=%d", section_name, len(content_blocks))
        return True

    def _append_section(self, doc: Document, section_name: str, content: str) -> None:
        """Fallback: append section at end of document."""
        doc.add_heading(section_name.title(), level=1)
        for block in content.split("\n\n"):
            if block.strip():
                doc.add_paragraph(block.strip())

    def _find_heading_in_tables(self, doc: Document, keyword: str):
        """Search table cells for a heading paragraph matching keyword.

        Returns the parent <w:tbl> XML element so content can be inserted after
        the entire table, or None if not found.
        """
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        if self._is_heading_style(para) and keyword.lower() in para.text.lower():
                            # para._element → <w:p>  parent → <w:tc>  parent → <w:tr>  parent → <w:tbl>
                            return para._element.getparent().getparent().getparent()
        return None

    @staticmethod
    def _inject_blocks_after_element(anchor_elem, content: str) -> None:
        """Insert content paragraphs as next siblings of anchor_elem.

        Inserts in reverse order so that block[0] ends up immediately after anchor_elem.
        """
        content_blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
        for block in reversed(content_blocks):
            new_para = OxmlElement("w:p")
            new_run = OxmlElement("w:r")
            new_text = OxmlElement("w:t")
            new_text.text = block
            new_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            new_run.append(new_text)
            new_para.append(new_run)
            anchor_elem.addnext(new_para)

    @staticmethod
    def _is_heading_style(para) -> bool:
        """Return True if the paragraph uses any Heading-based style (including custom derived styles)."""
        style = para.style
        while style is not None:
            if style.name.startswith("Heading"):
                return True
            style = getattr(style, "base_style", None)
        return False

    @staticmethod
    def _get_heading_level(para) -> int | None:
        style_name = para.style.name
        if style_name.startswith("Heading"):
            try:
                return int(style_name.split()[-1])
            except (ValueError, IndexError):
                return 1
        return None
