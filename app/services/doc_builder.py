"""Document assembly services."""

from __future__ import annotations

import datetime
import logging
import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches

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

# Sections whose template body content should be fully cleared before LLM injection.
# Use for sections that are 100% LLM-generated with no useful template intro text.
_FULL_CLEAR_SECTIONS = frozenset({
    "ARCHITECTURE COMPONENTS",
})

# Sections whose LLM output uses sub-topic labels (Networking, Security, Compute …)
# and should be formatted with bold labels + sentence-level bullet points.
_LABELED_FORMAT_SECTIONS = frozenset({
    "ARCHITECTURE COMPONENTS",
    "IMPLEMENTATION DETAILS",
})

# Known sub-topic label set for LABELED_FORMAT_SECTIONS
_SUB_TOPIC_LABELS = frozenset({
    "networking",
    "security",
    "compute",
    "storage and databases",
    "devops and management",
    "storage",
    "databases",
})

# Regex to split prose into sentences at ". " followed by an uppercase letter
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')

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
    r"|Relevant aspects of the architecture, how to deploy.*$"
    r"|Desired project outcome is to be provided by"
    r"|If the Statement of Work is filled in separately"
    r"|Initial understanding of the scope"
    r"|Desired Outcome, as jointly agreed"
    r"|Any change in the objectives and scope",
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
    "Oracle and ",              # "shared between Oracle and [customer]"
    "initial format by ",
    "granted for ",
    "the  Oracle",              # "the [customer] Oracle Labs" — blank INSIDE run
    "under the  Oracle",
    "based on the ",
    "falls under ",
    "fall under ",
    "ACE/Sales and ",
    "for the ",
    "provided by ",
    "under ",
    "The ",
)


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

        Operates at the XML level so that ALL paragraphs are covered,
        including those inside text boxes (w:txbxContent) and table cells
        which are not surfaced by doc.paragraphs.

        Three passes per paragraph:
        1. Replace literal placeholder tokens ('Customer1', 'Project1') in
           every run's <w:t> text, with an anti-duplication check against
           the following run.
        2. Fill blank/space-only runs that sit between context runs where
           the customer name belongs (identified by the preceding run's
           known suffix).  Includes a guard against filling when the next
           run already starts with the customer name.
        3. Collapse consecutive runs whose text equals the customer name
           (happens when the template had both a 'Customer1' token and an
           adjacent already-filled run).
        4. Blank run at paragraph start (i==0): fill when the paragraph has
           other non-empty content and does not already contain the name.
        """
        if not self.customer_name and not self.project_name:
            return

        token_map: dict[str, str] = {}
        if self.customer_name:
            token_map["Customer1"] = self.customer_name
        if self.project_name:
            token_map["Project1"] = self.project_name

        def _process_p_element(p_elem) -> None:
            r_elems = p_elem.findall(qn("w:r"))
            if not r_elems:
                return

            def _get_t(r) -> object | None:
                return r.find(qn("w:t"))

            def _text(r) -> str:
                t = _get_t(r)
                return (t.text or "") if t is not None else ""

            def _set_text(r, value: str) -> None:
                t = _get_t(r)
                if t is not None:
                    t.text = value
                    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                else:
                    new_t = OxmlElement("w:t")
                    new_t.text = value
                    new_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    r.append(new_t)

            # Pass 1: literal token replacement
            for idx, r in enumerate(r_elems):
                for token, replacement in token_map.items():
                    current = _text(r)
                    if token in current:
                        new_val = current.replace(token, replacement)
                        # Anti-duplication: if next run already starts with
                        # the replacement we just inserted, strip it there.
                        if idx + 1 < len(r_elems):
                            next_text = _text(r_elems[idx + 1])
                            if next_text.startswith(replacement):
                                _set_text(r_elems[idx + 1], next_text[len(replacement):])
                        _set_text(r, new_val)

            # Refresh run texts after pass 1
            run_texts = [_text(r) for r in r_elems]
            para_text = "".join(run_texts)

            if not self.customer_name:
                return

            # Pass 2: fill blank/space-only runs by context suffix
            # Only fills a blank run when the PRECEDING run ends with one of
            # the known prefix strings — this avoids false positives on blank
            # runs inside headings, table cells, and other non-name slots.
            for i, r in enumerate(r_elems):
                txt = run_texts[i]
                if txt.strip() != "":
                    continue  # not a blank run

                if i == 0:
                    continue  # skip leading blank runs — no preceding context

                # Guard: if the following run already starts with the
                # customer name, this blank run is adjacent to the real
                # value — do not fill to avoid duplication.
                if i + 1 < len(r_elems) and run_texts[i + 1].startswith(self.customer_name):
                    continue

                prev_text = run_texts[i - 1]
                for suffix in _CUSTOMER_PREFIX_SUFFIXES:
                    if prev_text.endswith(suffix):
                        _set_text(r, self.customer_name)
                        run_texts[i] = self.customer_name
                        logger.debug(
                            "doc_builder.customer_name_injected run_idx=%d prev_suffix=%r",
                            i, suffix,
                        )
                        break

            # Pass 3: collapse consecutive customer-name runs
            # (template had two adjacent placeholder runs for the same slot)
            last_name_idx: int | None = None
            run_texts = [_text(r) for r in r_elems]  # refresh
            for i, r in enumerate(r_elems):
                txt = run_texts[i]
                if txt == self.customer_name:
                    if last_name_idx is not None:
                        _set_text(r, "")  # clear the duplicate
                        logger.debug("doc_builder.customer_name_deduped run_idx=%d", i)
                    else:
                        last_name_idx = i
                elif txt.strip():
                    last_name_idx = None  # reset on non-empty, non-name text

        # Iterate over EVERY <w:p> in the document, including those inside
        # text boxes (w:txbxContent), headers, footers, and table cells.
        for p_elem in doc.element.body.iter(qn("w:p")):
            try:
                _process_p_element(p_elem)
            except Exception:
                logger.debug("doc_builder.substitute_names_para_error", exc_info=True)

        logger.info(
            "doc_builder.names_substituted customer=%r project=%r",
            self.customer_name,
            self.project_name,
        )

    def build(
        self,
        sections: list[tuple[str, str]],
        output_dir: Path,
        diagram_images: dict[str, bytes] | None = None,
        project_context: dict | None = None,
    ) -> str:
        """Inject sections into template headings and save DOCX.

        Args:
            sections: List of (section_name, content) tuples in canonical order.
            output_dir: Directory where the output DOCX is written.
            diagram_images: Optional mapping of ``"current"`` / ``"target"`` → raw
                PNG/JPG bytes of the architecture diagram images to embed in the
                corresponding placeholder boxes in the template.
            project_context: Full project context dict from the API request (client,
                project_name, scope, industry, services …).  Used to populate
                Company Profile, In Scope Application, and Acceptance Criteria tables.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"output_{uuid4().hex}.docx"
        output_path = output_dir / output_name

        doc = self._load_or_create_template()
        self._substitute_names(doc)
        self._fill_project_tables(doc, project_context=project_context)

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

        if diagram_images:
            self._insert_diagram_images(doc, diagram_images)

        self._apply_heading1_page_breaks(doc)
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

    # ------------------------------------------------------------------
    # Diagram image insertion  (Feature B)
    # ------------------------------------------------------------------

    def _insert_diagram_images(self, doc: Document, diagram_images: dict[str, bytes]) -> None:
        """Replace placeholder images with the actual uploaded architecture diagrams.

        Searches for the headings "Current State Architecture - Diagram" and
        "Target Architecture Diagram" in the document, finds the placeholder
        drawing paragraph that follows each heading (within 10 paragraphs), clears
        it, and inserts the real image as a 5.5-inch wide inline picture.

        Args:
            diagram_images: Mapping of ``"current"`` or ``"target"`` → raw image bytes.
        """
        _DRAWING_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        _DRAWING_TAG = f"{{{_DRAWING_NS}}}drawing"

        slot_map = [
            ("current", "current state architecture - diagram"),
            ("target", "target architecture diagram"),
        ]

        all_paras = doc.paragraphs

        for role, keyword in slot_map:
            image_bytes = diagram_images.get(role)
            if not image_bytes:
                logger.debug("doc_builder.diagram_skip role=%s (no image bytes)", role)
                continue

            # Find the matching heading paragraph
            heading_idx: int | None = None
            for i, p in enumerate(all_paras):
                if keyword in p.text.lower() and self._is_heading_style(p):
                    heading_idx = i
                    break

            if heading_idx is None:
                logger.warning(
                    "doc_builder.diagram_heading_not_found role=%s keyword=%r", role, keyword
                )
                continue

            # Find the first paragraph after the heading that contains a drawing
            placeholder_para = None
            search_limit = min(heading_idx + 10, len(all_paras))
            for i in range(heading_idx + 1, search_limit):
                p = all_paras[i]
                if p._element.findall(f".//{_DRAWING_TAG}"):
                    placeholder_para = p
                    logger.debug(
                        "doc_builder.diagram_placeholder_found role=%s para_idx=%d", role, i
                    )
                    break

            if placeholder_para is None:
                # No existing placeholder — insert a new empty paragraph right after
                # the heading and use it as the target.
                logger.info(
                    "doc_builder.diagram_no_placeholder role=%s — inserting after heading", role
                )
                new_p_elem = OxmlElement("w:p")
                all_paras[heading_idx]._element.addnext(new_p_elem)
                # Retrieve as Paragraph object
                placeholder_para = next(
                    (p for p in doc.paragraphs if p._element is new_p_elem), None
                )
                if placeholder_para is None:
                    logger.warning(
                        "doc_builder.diagram_para_create_failed role=%s", role
                    )
                    continue

            # Clear existing content, preserving paragraph properties (<w:pPr>)
            p_elem = placeholder_para._element
            pPr = p_elem.find(qn("w:pPr"))
            # Remove all children except pPr
            for child in list(p_elem):
                if child is not pPr:
                    p_elem.remove(child)

            # Embed the image via python-docx (handles relationship registration)
            try:
                run = placeholder_para.add_run()
                run.add_picture(BytesIO(image_bytes), width=Inches(5.5))
                logger.info(
                    "doc_builder.diagram_image_inserted role=%s bytes=%d",
                    role,
                    len(image_bytes),
                )
            except Exception:
                logger.exception(
                    "doc_builder.diagram_image_insert_failed role=%s", role
                )

    # ------------------------------------------------------------------
    # Page break before every Heading 1
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_heading1_page_breaks(doc: Document) -> None:
        """Add a pageBreakBefore property to every Heading 1 except the first.

        This ensures each major section starts on a fresh page without the engineer
        having to manually insert page breaks after every generation run.
        """
        first_h1_seen = False
        for para in doc.paragraphs:
            if not (para.style.name == "Heading 1" or
                    (para.style.name.startswith("Heading") and
                     DocumentBuilder._get_heading_level(para) == 1)):
                continue
            if not first_h1_seen:
                first_h1_seen = True
                continue  # don't add a break before the very first H1
            p_elem = para._element
            pPr = p_elem.find(qn("w:pPr"))
            if pPr is None:
                pPr = OxmlElement("w:pPr")
                p_elem.insert(0, pPr)
            if pPr.find(qn("w:pageBreakBefore")) is None:
                pbr = OxmlElement("w:pageBreakBefore")
                pPr.append(pbr)
        logger.info("doc_builder.heading1_page_breaks_applied")

    # ------------------------------------------------------------------
    # Formatted block injection (bold labels + bullet sentences)
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_formatted_blocks_after_element(anchor_elem, content: str) -> None:
        """Insert content using bold sub-topic labels and bullet-point sentences.

        Used for ARCHITECTURE COMPONENTS and IMPLEMENTATION DETAILS.  The LLM
        output for these sections looks like::

            Networking\\nDetails about the network...\\n\\nSecurity\\nDetails...

        Each block whose first line is a known sub-topic label (Networking, Security,
        Compute, Storage and Databases, DevOps and Management) is formatted as:

        - A bold paragraph for the label
        - One bullet paragraph per sentence in the body (prefix ``– ``)

        Other blocks (e.g. introductory paragraphs) are inserted as plain paragraphs.

        Inserts in reverse order so that block[0] ends up immediately after anchor_elem.
        """
        blocks = [b.strip() for b in content.split("\n\n") if b.strip()]

        def _make_para(text: str, bold: bool = False) -> object:
            new_para = OxmlElement("w:p")
            new_run = OxmlElement("w:r")
            if bold:
                rPr = OxmlElement("w:rPr")
                bold_elem = OxmlElement("w:b")
                rPr.append(bold_elem)
                new_run.append(rPr)
            new_text = OxmlElement("w:t")
            new_text.text = text
            new_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            new_run.append(new_text)
            new_para.append(new_run)
            return new_para

        for block in reversed(blocks):
            lines = block.split("\n", 1)
            first_line = lines[0].strip().rstrip(":")
            body = lines[1].strip() if len(lines) > 1 else ""
            is_label = first_line.lower() in _SUB_TOPIC_LABELS

            if is_label and body:
                # Insert body as bullet sentences (reversed so first ends up first)
                sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
                if not sentences:
                    sentences = [body]
                for sentence in reversed(sentences):
                    anchor_elem.addnext(_make_para("– " + sentence))
                # Insert bold label after anchor (so it comes before the bullets)
                anchor_elem.addnext(_make_para(first_line, bold=True))
            else:
                anchor_elem.addnext(_make_para(block))

    # ------------------------------------------------------------------
    # Table data filling
    # ------------------------------------------------------------------

    # Sample acceptance criteria for an OCI cloud migration / deployment project.
    _SAMPLE_ACCEPTANCE_CRITERIA: list[tuple[str, str]] = [
        (
            "Deployment completeness",
            "All in-scope application components are deployed and operational on OCI "
            "(OKE workloads, MySQL Database System, Object Storage, Load Balancer).",
        ),
        (
            "Performance baseline",
            "Application response time remains within agreed SLA targets under "
            "production-representative load; no degradation vs. on-premises baseline.",
        ),
        (
            "High availability",
            "Failover behaviour validated: single-node failure does not cause "
            "service outage; uptime target ≥ 99.9 % over the acceptance window.",
        ),
        (
            "Security posture",
            "WAF policy active, Vault-managed secrets in use, network segmentation "
            "verified, and no critical/high findings in the post-deployment security review.",
        ),
        (
            "CI/CD pipeline",
            "End-to-end automated build and deployment pipeline executes successfully "
            "on OCI; rollback procedure documented and tested.",
        ),
    ]

    def _fill_project_tables(
        self, doc: Document, project_context: dict | None = None
    ) -> None:
        """Fill structured table data in the DOCX template.

        Actions performed:
        - Version History: today's date when Revision Date cell is blank/placeholder.
        - Company Profile: legal name, industry from project context.
        - In Scope Application: application name, general description from context.
        - Acceptance Criteria: sample criteria for an OCI deployment project.
        - All tables: replace ``DD-MM-YYYY`` date placeholders with em-dash.
        """
        today_str = datetime.date.today().strftime("%d-%m-%Y")
        _DATE_PH = re.compile(r"^D[D\-]+M[M\-]+Y+$", re.IGNORECASE)
        ctx = project_context or {}

        for table in doc.tables:
            if not table.rows:
                continue

            headers = [
                cell.text.strip().lower().replace("\n", " ")
                for cell in table.rows[0].cells
            ]
            h0 = headers[0] if headers else ""

            # ── Version History ────────────────────────────────────────
            is_version_table = (
                "revision date" in headers
                or ("version #" in headers and "revised by" in headers)
                or ("version" in headers and "revised by" in headers)
            )
            if is_version_table and len(table.rows) > 1:
                data_row = table.rows[1]
                for ci, header in enumerate(headers):
                    if ci >= len(data_row.cells):
                        break
                    cell = data_row.cells[ci]
                    cell_text = cell.text.strip()
                    if "date" in header and (not cell_text or _DATE_PH.match(cell_text)):
                        self._set_cell_text(cell, today_str)
                        logger.info("doc_builder.version_history_date_set date=%s", today_str)
                    elif "revised by" in header and not cell_text:
                        self._set_cell_text(cell, "Oracle Labs")
                        logger.info("doc_builder.version_history_author_set")
                continue  # DD-MM-YYYY pass handled above

            # ── Company Profile ────────────────────────────────────────
            # Detected by "legal name" appearing in the first column headers.
            if any("legal name" in h for h in headers):
                for row in table.rows:
                    label = row.cells[0].text.strip().lower() if row.cells else ""
                    val_cell = row.cells[1] if len(row.cells) > 1 else None
                    if val_cell is None:
                        continue
                    current = val_cell.text.strip()
                    if not current or current == " ":
                        if "legal name" in label:
                            self._set_cell_text(val_cell, self.customer_name)
                        elif "industry" in label or "selling" in label:
                            industry = ctx.get("industry") or ""
                            if industry:
                                self._set_cell_text(val_cell, industry)
                logger.info("doc_builder.company_profile_filled")
                continue

            # ── In Scope Application ───────────────────────────────────
            # Detected by first header = "application name".
            if "application name" in h0:
                for row in table.rows:
                    label = row.cells[0].text.strip().lower() if row.cells else ""
                    val_cell = row.cells[1] if len(row.cells) > 1 else None
                    if val_cell is None:
                        continue
                    current = val_cell.text.strip()
                    if "application name" in label:
                        # Always overwrite to fix the Customer1Project1 concatenation
                        app_name = f"{self.customer_name}: {self.project_name}"
                        self._set_cell_text(val_cell, app_name.strip(": "))
                    elif "general description" in label and not current:
                        scope = ctx.get("scope") or ""
                        if scope:
                            self._set_cell_text(val_cell, scope)
                    elif "running on" in label and not current:
                        self._set_cell_text(val_cell, "On-premises → OCI (Oracle Cloud Infrastructure)")
                logger.info("doc_builder.in_scope_application_filled")
                continue

            # ── Acceptance Criteria ────────────────────────────────────
            # Detected by "acceptance criteria" in col 1 of the header row.
            if len(headers) > 1 and "acceptance criteria" in headers[1]:
                data_rows = table.rows[1:]  # skip header row
                for ri, (capability, description) in enumerate(self._SAMPLE_ACCEPTANCE_CRITERIA):
                    if ri >= len(data_rows):
                        break
                    row = data_rows[ri]
                    if len(row.cells) >= 2:
                        self._set_cell_text(row.cells[0], capability)
                        self._set_cell_text(row.cells[1], description)
                        if len(row.cells) >= 3:
                            self._set_cell_text(row.cells[2], "Pending")
                        if len(row.cells) >= 4:
                            self._set_cell_text(row.cells[3], "—")
                logger.info("doc_builder.acceptance_criteria_filled")
                continue

            # ── General DD-MM-YYYY placeholder sweep ──────────────────
            for row in table.rows[1:]:
                for cell in row.cells:
                    if _DATE_PH.match(cell.text.strip()):
                        self._set_cell_text(cell, "\u2014")  # em-dash

        logger.info("doc_builder.project_tables_filled")

    @staticmethod
    def _set_cell_text(cell, text: str) -> None:
        """Set the text of the first paragraph in a table cell, preserving formatting."""
        for para in cell.paragraphs:
            # Clear all runs
            for run in para.runs:
                run.text = ""
            # Write into the first run, or create one
            if para.runs:
                para.runs[0].text = text
            else:
                para.add_run(text)
            return  # only touch the first paragraph

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
        content_blocks = [b.strip() for b in content.split("\n\n") if b.strip()]

        # ── Full-clear sections ──────────────────────────────────────────
        # For sections that are 100% LLM-generated, remove ALL non-heading
        # template paragraphs between the section heading and the next
        # heading, then inject the LLM content immediately after the heading.
        if section_name.upper() in _FULL_CLEAR_SECTIONS:
            for i in range(heading_idx + 1, next_heading_idx):
                para = paragraphs[i]
                if self._get_heading_level(para) is None:
                    try:
                        body.remove(para._element)
                    except Exception:
                        pass
            anchor_elem = paragraphs[heading_idx]._element
            _use_formatted = section_name.upper() in _LABELED_FORMAT_SECTIONS
            if _use_formatted:
                self._inject_formatted_blocks_after_element(anchor_elem, content)
            else:
                self._inject_blocks_after_element(anchor_elem, content)
            logger.info(
                "doc_builder.section_injected section=%s blocks=%d (full_clear%s)",
                section_name, len(content_blocks),
                ",formatted" if _use_formatted else "",
            )
            return True

        # ── Normal sections ──────────────────────────────────────────────
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

        if section_name.upper() in _LABELED_FORMAT_SECTIONS:
            self._inject_formatted_blocks_after_element(anchor_elem, content)
        else:
            self._inject_blocks_after_element(anchor_elem, content)
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
