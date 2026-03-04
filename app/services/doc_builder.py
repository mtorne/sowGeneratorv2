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
    # "Scope" H3 lives inside Project Overview H1.
    # Prefix "=" means exact-match (avoids matching "In Scope Application").
    "SCOPE":                                        "=scope",
    "CURRENT STATE ARCHITECTURE":                   "current state architecture",
    # H3 sub-section "Current State Architecture - Description"
    "CURRENT STATE ARCHITECTURE DESCRIPTION":       "current state architecture - description",
    "CURRENTLY USED TECHNOLOGY STACK":              "currently used technology",
    "OCI SERVICE SIZING AND AMOUNTS":               "oci service sizing",
    "FUTURE STATE ARCHITECTURE":                    "future state architecture",
    "ARCHITECTURE DEPLOYMENT OVERVIEW":             "architecture deployment",
    "ARCHITECTURE COMPONENTS":                      "architecture components",
    "IMPLEMENTATION DETAILS":                       "implementation details",
    "SECURITY":                                     "security",
    "HIGH AVAILABILITY":                            "high availability",
    "MANAGED SERVICES CONFIGURATION":               "managed services",
    # Architect review — appended at end when not found in template heading
    "ARCHITECT REVIEW":                             "architect review",
    "CLOSING FEEDBACK":                             "closing feedback",
}

# Sections whose template body content should be fully cleared before LLM injection.
# Use for sections that are 100% LLM-generated with no useful template intro text.
_FULL_CLEAR_SECTIONS = frozenset({
    "ARCHITECTURE COMPONENTS",
    # NOTE: SCOPE is intentionally NOT here — the template colored-box headers
    # ("Initial understanding of the scope", "Desired Outcome of customer",
    # "Desired outcome agreed with Oracle") must survive so users can fill them.
    # The Description H3 sub-section has only generic placeholder intro text.
    "CURRENT STATE ARCHITECTURE DESCRIPTION",
    # STATUS AND NEXT STEPS has only generic template intro text ("Current status
    # and what needs to happen next…") — replace it entirely with the LLM output.
    "STATUS AND NEXT STEPS",
    # OCI SERVICE SIZING AND AMOUNTS — the template has several generic intro
    # paragraphs; replace them all with the single LLM-generated sentence.
    "OCI SERVICE SIZING AND AMOUNTS",
})

# Sections where LLM content should be injected RIGHT AFTER the heading,
# BEFORE any surviving template paragraphs.
# SCOPE was removed from this set — it now uses _inject_scope_boxes() which
# removes only LLM-generated intro text and injects content into the colored
# scope-box paragraphs via soft breaks, leaving template text intact.
_INJECT_AT_TOP_SECTIONS: frozenset[str] = frozenset()

# Sections whose LLM output uses sub-topic labels (Networking, Security, Compute …)
# and should be formatted with bold labels + sentence-level bullet points.
_LABELED_FORMAT_SECTIONS = frozenset({
    "ARCHITECTURE COMPONENTS",
    "IMPLEMENTATION DETAILS",
    # Architect review uses the same label+bullet format for its sub-sections.
    "ARCHITECT REVIEW",
    # Status and next steps uses Completed / Pending labels + bullet lines.
    "STATUS AND NEXT STEPS",
})

# Sections whose LLM output uses hierarchical bullet lines (L1/L2/L3 indentation).
# Each newline-separated line becomes its own paragraph; leading spaces determine
# the indent level: 0 → L1 (0 twips), 2 spaces → L2 (360 twips), 4+ → L3 (720 twips).
_HIERARCHICAL_BULLET_SECTIONS = frozenset({
    "CURRENT STATE ARCHITECTURE DESCRIPTION",
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
    # Architect Review sub-sections
    "generation quality",
    "data gaps",
    "next steps",
    "recommendations",
    # Status and Next Steps sub-sections
    "completed",
    "pending",
})

# Regex to split prose into sentences at ". " followed by an uppercase letter
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')

# Literal string placed by LLM for unknown values — rendered red in the DOCX.
_PENDING_MARKER = "PENDING TO REVIEW"

# Placeholder text patterns to remove when found inside a section.
# NOTE: "Initial understanding of the scope", "Desired Outcome, as jointly agreed",
# and "Any change in the objectives and scope" have been deliberately removed so
# the template's colored scope-box headers survive injection.
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
    r"|If the Statement of Work is filled in separately",
    re.IGNORECASE,
)

# Heading title suffixes (lower-case, without leading customer name) that mark
# headings whose FIRST run is a blank placeholder for the customer name.
_CUSTOMER_HEADING_PREFIXES: frozenset[str] = frozenset({
    "company profile",
    "offboarding",
})

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


def _build_para_elem(text: str, bold: bool = False) -> object:
    """Build a ``<w:p>`` XML element containing one or more runs.

    If *text* contains the :data:`_PENDING_MARKER` literal, the marker
    segments are emitted as separate red-coloured runs so they appear in
    red in the rendered DOCX.  All other text is emitted in the normal
    (or bold) colour.
    """
    new_para = OxmlElement("w:p")

    def _add_run(t: str, red: bool = False) -> None:
        if not t:
            return
        r = OxmlElement("w:r")
        if bold or red:
            rPr = OxmlElement("w:rPr")
            if bold:
                rPr.append(OxmlElement("w:b"))
            if red:
                c = OxmlElement("w:color")
                c.set(qn("w:val"), "FF0000")
                rPr.append(c)
            r.append(rPr)
        t_elem = OxmlElement("w:t")
        t_elem.text = t
        t_elem.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r.append(t_elem)
        new_para.append(r)

    if _PENDING_MARKER in text:
        parts = text.split(_PENDING_MARKER)
        for idx, part in enumerate(parts):
            _add_run(part, red=False)
            if idx < len(parts) - 1:
                _add_run(_PENDING_MARKER, red=True)
    else:
        _add_run(text, red=False)

    return new_para


def _build_indented_para_elem(text: str, left_twips: int = 0) -> object:
    """Build a ``<w:p>`` with optional left indentation.

    Like :func:`_build_para_elem` but supports hierarchical indentation for
    bullet lists.  The *left_twips* value is applied as ``<w:ind w:left>``.
    ``PENDING TO REVIEW`` markers are rendered in red, same as _build_para_elem.
    """
    new_para = OxmlElement("w:p")

    if left_twips:
        pPr = OxmlElement("w:pPr")
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), str(left_twips))
        pPr.append(ind)
        new_para.insert(0, pPr)

    def _add_run(t: str, red: bool = False) -> None:
        if not t:
            return
        r = OxmlElement("w:r")
        if red:
            rPr = OxmlElement("w:rPr")
            c = OxmlElement("w:color")
            c.set(qn("w:val"), "FF0000")
            rPr.append(c)
            r.append(rPr)
        t_elem = OxmlElement("w:t")
        t_elem.text = t
        t_elem.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r.append(t_elem)
        new_para.append(r)

    if _PENDING_MARKER in text:
        parts = text.split(_PENDING_MARKER)
        for idx, part in enumerate(parts):
            _add_run(part, red=False)
            if idx < len(parts) - 1:
                _add_run(_PENDING_MARKER, red=True)
    else:
        _add_run(text)

    return new_para


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
            # Direct-child runs — used for Pass 2 / 3 context-based substitution.
            r_elems = p_elem.findall(qn("w:r"))

            # Word templates often store Customer1 / Project1 as a DOCPROPERTY
            # field: <w:fldSimple w:instr="DOCPROPERTY 01_Customer …"><w:r>…</w:r>
            # </w:fldSimple>.  The run inside the field is a grandchild of <w:p>,
            # not a direct child, so findall(qn("w:r")) would miss it.  Collect
            # those extra runs for Pass 1 (literal token replacement) only.
            field_runs = [
                r
                for fld in p_elem.findall(qn("w:fldSimple"))
                for r in fld.findall(qn("w:r"))
            ]
            all_r_for_pass1 = list(r_elems) + field_runs

            if not all_r_for_pass1:
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

            # Pass 1: literal token replacement (direct runs + fldSimple field runs)
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
            # Also replace in fldSimple nested runs (no anti-dup needed — these
            # are isolated field runs that don't have adjacent context runs).
            for r in field_runs:
                for token, replacement in token_map.items():
                    current = _text(r)
                    if token in current:
                        _set_text(r, current.replace(token, replacement))

            if not r_elems:
                return  # no direct-child runs → skip Pass 2/3

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
                    # Special case: leading blank run in a heading whose title
                    # (formed by joining all SUBSEQUENT runs) is a known
                    # "customer-prefixed" heading (e.g. " Company Profile").
                    # Fill it with the customer name only when the paragraph
                    # text does not already contain the customer name.
                    if not self.customer_name:
                        continue
                    if self.customer_name in para_text:
                        continue  # already substituted — skip
                    rest_text = "".join(run_texts[1:]).strip().lower()
                    if rest_text in _CUSTOMER_HEADING_PREFIXES:
                        # Preserve a space separator between customer name and
                        # the heading title that follows in the next run.
                        fill = self.customer_name
                        if (
                            i + 1 < len(r_elems)
                            and not run_texts[i + 1].startswith(" ")
                        ):
                            fill = self.customer_name + " "
                        _set_text(r, fill)
                        run_texts[i] = fill
                        logger.debug(
                            "doc_builder.customer_name_injected_heading rest=%r", rest_text
                        )
                    continue  # no preceding context for suffix-based fill

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
        self._suppress_blank_heading_page_breaks(doc)
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

    @staticmethod
    def _suppress_blank_heading_page_breaks(doc: Document) -> None:
        """Remove spurious page breaks that produce blank pages.

        Two passes — both operate only on paragraphs whose visible text is
        empty or whitespace-only (so content paragraphs are never touched):

        **Pass 1 — ``<w:pageBreakBefore/>`` on empty headings.**
        ``_apply_heading1_page_breaks`` adds this element to every H1 except
        the first.  Empty H1 paragraphs left in the template trigger a blank
        page because Word honours the page-break even though nothing is
        rendered.

        **Pass 2 — explicit ``<w:br w:type="page"/>`` runs in empty paragraphs.**
        Word / template editors sometimes insert a lone page-break run inside
        an empty Normal paragraph immediately following an empty heading,
        compounding the blank-page problem.  Removing the ``<w:br>`` element
        (and the now-empty run that contained it) eliminates the extra page.

        IMPORTANT: paragraphs that contain inline images have ``para.text == ""``
        but their runs hold ``<w:drawing>`` elements.  We must never touch those
        runs — only runs that are truly devoid of any visible content.
        """
        # Tags that represent real content inside a run.  A run carrying any of
        # these must NOT be deleted, even if it has no plain text.
        _CONTENT_RUN_TAGS = {
            qn("w:t"),        # text
            qn("w:br"),       # line / page break
            qn("w:drawing"),  # inline image / shape
            qn("w:pict"),     # legacy VML picture
            qn("w:object"),   # embedded OLE object
            qn("w:sym"),      # symbol glyph
            qn("w:tab"),      # tab character
        }

        removed_pbr = 0  # pageBreakBefore on empty headings
        removed_brk = 0  # explicit page-break runs in empty paragraphs

        for para in doc.paragraphs:
            if para.text.strip():
                continue  # paragraph has visible text — leave page breaks alone

            # Skip paragraphs that contain images / drawings (para.text is "" for those)
            p_elem = para._element
            if p_elem.findall(f".//{qn('w:drawing')}") or p_elem.findall(f".//{qn('w:pict')}"):
                continue

            # Pass 1: pageBreakBefore on empty headings
            if DocumentBuilder._get_heading_level(para) is not None:
                pPr = p_elem.find(qn("w:pPr"))
                if pPr is not None:
                    pbr = pPr.find(qn("w:pageBreakBefore"))
                    if pbr is not None:
                        pPr.remove(pbr)
                        removed_pbr += 1

            # Pass 2: explicit page-break runs in empty paragraphs
            for r_elem in list(p_elem.findall(qn("w:r"))):
                for br_elem in list(r_elem.findall(qn("w:br"))):
                    if br_elem.get(qn("w:type")) == "page":
                        r_elem.remove(br_elem)
                        removed_brk += 1
                # Drop the run element only if it has no meaningful content at all
                has_content = any(child.tag in _CONTENT_RUN_TAGS for child in r_elem)
                if not has_content:
                    p_elem.remove(r_elem)

        if removed_pbr:
            logger.info("doc_builder.blank_heading_page_breaks_suppressed count=%d", removed_pbr)
        if removed_brk:
            logger.info("doc_builder.blank_para_page_breaks_suppressed count=%d", removed_brk)

    # ------------------------------------------------------------------
    # SCOPE colored-box content injection
    # ------------------------------------------------------------------

    # Mapping from LLM section label keywords (lower-case, partial match) to
    # the zero-based index of the scope colored box it belongs to.
    _SCOPE_LABEL_TO_BOX: list[tuple[str, int]] = [
        ("initial understanding", 0),
        ("initial scope",         0),
        ("customer desired outcome", 1),
        ("desired outcome (customer)", 1),
        ("customer perspective",  1),
        ("desired outcome of",    1),
        ("agreed outcome",        2),
        ("desired outcome (agreed)", 2),
        ("jointly agreed",        2),
        ("oracle",                2),   # fallback for "agreed with Oracle" phrases
    ]

    def _inject_scope_boxes(
        self,
        doc: Document,
        heading_idx: int,
        next_heading_idx: int,
        content: str,
    ) -> None:
        """Handle SCOPE section injection.

        Unlike every other section, SCOPE retains its template infrastructure
        (the three colored paragraph boxes) and injects LLM content INSIDE
        each box via soft-return runs.  LLM-generated intro paragraphs that
        were inserted before the boxes in an earlier pass are removed.

        Algorithm
        ---------
        1. Walk paragraphs between the Scope heading and the next heading.
        2. Paragraphs that have **both** ``<w:pBdr>`` and ``<w:shd>`` are the
           colored scope boxes — collect them.
        3. Paragraphs that appear **before** the first box AND have no
           ``w:rsidR`` attribute (i.e. were injected by the LLM pipeline, not
           present in the original template) are removed as stale intro text.
        4. Parse the LLM ``content`` into up to three labeled blocks using
           :data:`_SCOPE_LABEL_TO_BOX`.
        5. For each box paragraph, append a ``<w:br w:type="textWrapping"/>``
           soft break followed by the matching LLM text as new ``<w:r>`` runs.
           This keeps all content inside the box border.
        """
        paragraphs = list(doc.paragraphs)
        body = doc.element.body
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        box_paras: list = []
        pre_box_llm_paras: list = []
        first_box_found = False

        for i in range(heading_idx + 1, next_heading_idx):
            para = paragraphs[i]
            p = para._element
            has_pBdr = p.find(f".//{qn('w:pBdr')}") is not None
            has_shd  = p.find(f".//{qn('w:shd')}")  is not None
            is_box   = has_pBdr and has_shd

            if is_box:
                box_paras.append(para)
                first_box_found = True
            elif not first_box_found and para.text.strip():
                # Non-empty para before any box — check if it is LLM-injected
                # (no w:rsidR attribute) or a template para (has w:rsidR).
                has_rsid = p.get(f"{{{W}}}rsidR") is not None
                if not has_rsid:
                    pre_box_llm_paras.append(para)

        # Remove stale LLM intro paragraphs
        for para in pre_box_llm_paras:
            try:
                body.remove(para._element)
                logger.debug("doc_builder.scope_intro_removed text=%r", para.text[:60])
            except Exception:
                pass

        if not box_paras:
            logger.warning("doc_builder.scope_no_boxes_found — falling back to append")
            anchor = paragraphs[heading_idx]._element
            self._inject_blocks_after_element(anchor, content)
            return

        # ── Parse LLM output into 3 labeled blocks ────────────────────────
        blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
        box_contents: list[str] = ["", "", ""]
        current_box = 0

        for block in blocks:
            lines   = block.split("\n", 1)
            label   = lines[0].strip().rstrip(":").lower()
            body_text = lines[1].strip() if len(lines) > 1 else ""

            matched = False
            for keyword, idx in self._SCOPE_LABEL_TO_BOX:
                if keyword in label:
                    current_box = idx
                    box_contents[idx] = body_text or block
                    matched = True
                    break
            if not matched:
                # No recognised label — treat whole block as content for current box
                if box_contents[current_box]:
                    box_contents[current_box] += " " + block
                else:
                    box_contents[current_box] = block

        # ── Inject into box paragraphs via soft break ──────────────────────
        for box_idx, (box_para, box_text) in enumerate(
            zip(box_paras, box_contents)
        ):
            if not box_text.strip():
                continue
            p_elem = box_para._element

            # Soft break so the injected text starts on a new visual line
            # inside the same paragraph (and therefore the same box border).
            br_run = OxmlElement("w:r")
            br_inner = OxmlElement("w:br")
            br_inner.set(qn("w:type"), "textWrapping")
            br_run.append(br_inner)
            p_elem.append(br_run)

            # One run per line of content.
            # Explicitly set color=000000 on every injected run so the text is
            # always black, regardless of the paragraph's inherited colour
            # (scope box paragraphs often carry a non-black rPr colour).
            for line in box_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                r_elem = OxmlElement("w:r")
                rPr = OxmlElement("w:rPr")
                clr = OxmlElement("w:color")
                clr.set(qn("w:val"), "000000")
                rPr.append(clr)
                r_elem.append(rPr)
                t_elem = OxmlElement("w:t")
                t_elem.text = line
                t_elem.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                r_elem.append(t_elem)
                p_elem.append(r_elem)

        logger.info(
            "doc_builder.scope_boxes_filled boxes=%d removed_intro=%d",
            len(box_paras),
            len(pre_box_llm_paras),
        )

    # ------------------------------------------------------------------
    # Formatted block injection (bold labels + bullet sentences)
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_formatted_blocks_after_element(anchor_elem, content: str) -> None:
        """Insert content using bold sub-topic labels and bullet-point sentences.

        Used for ARCHITECTURE COMPONENTS, IMPLEMENTATION DETAILS, and ARCHITECT
        REVIEW.  The LLM output for these sections looks like::

            Networking\\nDetails about the network...\\n\\nSecurity\\nDetails...

        Each block whose first line is a known sub-topic label (Networking, Security,
        Compute, Storage and Databases, DevOps and Management, Generation Quality,
        Data Gaps, Next Steps, Recommendations) is formatted as:

        - A bold paragraph for the label
        - One bullet paragraph per item (prefix ``– ``)

        Body items are split by newlines when the body is multi-line (e.g. bullet
        lists, numbered items from Architect Review).  For single-line dense prose
        the sentence-boundary splitter is used instead.

        Other blocks (e.g. introductory paragraphs) are inserted as plain paragraphs.

        Inserts in reverse order so that block[0] ends up immediately after anchor_elem.
        """
        blocks = [b.strip() for b in content.split("\n\n") if b.strip()]

        for block in reversed(blocks):
            lines = block.split("\n", 1)
            first_line = lines[0].strip().rstrip(":")
            body = lines[1].strip() if len(lines) > 1 else ""
            is_label = first_line.lower() in _SUB_TOPIC_LABELS

            if is_label and body:
                # Split body into individual items.  When the body is multi-line
                # (bullet lists, numbered steps) split on newlines so each item
                # gets its own paragraph.  For dense single-line prose, fall back
                # to the sentence-boundary splitter.
                if "\n" in body:
                    sentences = [s.strip() for s in body.split("\n") if s.strip()]
                else:
                    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
                if not sentences:
                    sentences = [body]
                # Insert bullet paragraphs in reverse so first ends up first.
                # Each line is built with _build_para_elem so PENDING TO REVIEW
                # markers are rendered in red.
                for sentence in reversed(sentences):
                    # Avoid double-prefixing lines that already carry a bullet
                    # marker (e.g. "– …" from Data Gaps or "1. …" from Next Steps).
                    prefix = "" if sentence.startswith(("–", "-", "•")) or (
                        len(sentence) > 1 and sentence[0].isdigit() and sentence[1] in ".)"
                    ) else "– "
                    anchor_elem.addnext(_build_para_elem(prefix + sentence))
                # Insert bold label after anchor (so it comes before the bullets)
                anchor_elem.addnext(_build_para_elem(first_line, bold=True))
            else:
                anchor_elem.addnext(_build_para_elem(block))

    @staticmethod
    def _inject_hierarchical_bullets_after_element(anchor_elem, content: str) -> None:
        """Inject hierarchical bullet content as separate indented paragraphs.

        Splits *content* on newlines.  Each non-empty line becomes its own
        ``<w:p>`` element inserted after *anchor_elem*.  Leading whitespace
        determines the indentation level:

        * 0 leading spaces  → L1 (``left=0`` twips)
        * 2 leading spaces  → L2 (``left=360`` twips ≈ quarter-inch)
        * 4+ leading spaces → L3 (``left=720`` twips ≈ half-inch)

        ``PENDING TO REVIEW`` markers are rendered in red.  Lines are inserted
        in reverse order so that line[0] ends up immediately after *anchor_elem*.
        """
        all_lines = [line for line in content.split("\n") if line.strip()]
        for line in reversed(all_lines):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if indent >= 4:
                left_twips = 720
            elif indent >= 2:
                left_twips = 360
            else:
                left_twips = 0
            anchor_elem.addnext(_build_indented_para_elem(stripped, left_twips))

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
        - Company Profile: legal name, country, industry from project context.
        - In Scope Application: application name, general description from context.
        - Application Details (General Aspects): arch type, languages, tech stack.
        - Database Tier: product, sizing, OS, scalability, availability, backup, security.
        - Application Tier: product, sizing, OS, features.
        - OCI Service Sizing / BOM: rows from inferred_metadata.oci_bom.
        - Acceptance Criteria: sample criteria for an OCI deployment project.
        - All tables: replace ``DD-MM-YYYY`` date placeholders with em-dash.
        """
        today_str = datetime.date.today().strftime("%d-%m-%Y")
        _DATE_PH = re.compile(r"^D[D\-]+M[M\-]+Y+$", re.IGNORECASE)
        ctx = project_context or {}
        meta = ctx.get("inferred_metadata") or {}

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
                    if "date" in header:
                        # Always overwrite — template may ship with a real-looking
                        # date (e.g. "01-10-2025") that _DATE_PH wouldn't match.
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
                        elif "country" in label:
                            country = meta.get("country") or ""
                            if country:
                                self._set_cell_text(val_cell, country)
                        elif "industry" in label or "selling" in label:
                            industry = (
                                meta.get("industry")
                                or ctx.get("industry")
                                or ""
                            )
                            if industry:
                                self._set_cell_text(val_cell, industry)
                        elif "description" in label or "company" in label:
                            desc = meta.get("company_description") or ""
                            if desc:
                                self._set_cell_text(val_cell, desc)
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
                        platform = ctx.get("cloud") or ""
                        val = (
                            f"{platform} → OCI (Oracle Cloud Infrastructure)"
                            if platform and platform.upper() not in {"OCI", "ORACLE"}
                            else "OCI (Oracle Cloud Infrastructure)"
                        )
                        self._set_cell_text(val_cell, val)
                logger.info("doc_builder.in_scope_application_filled")
                continue

            # ── Application Details (General Aspects) ─────────────────
            # Detected by first header = "general aspects".
            if "general aspects" in h0:
                _APP_DETAIL_MAP = {
                    "application architecture": "app_architecture_type",
                    "development language":     "development_languages",
                    "hardware dependencies":    "hardware_dependencies",
                    "used technologies":        "used_technologies",
                }
                for row in table.rows[1:]:  # skip header row
                    if len(row.cells) < 2:
                        continue
                    label = row.cells[0].text.strip().lower()
                    val_cell = row.cells[1]
                    if val_cell.text.strip():
                        continue  # already has content
                    for key_fragment, meta_key in _APP_DETAIL_MAP.items():
                        if key_fragment in label:
                            value = meta.get(meta_key) or ""
                            if value:
                                self._set_cell_text(val_cell, value)
                            break
                logger.info("doc_builder.app_details_filled")
                continue

            # ── Database Tier ──────────────────────────────────────────
            # Detected by first header = "database tier".
            if "database tier" in h0:
                _DB_MAP = {
                    "product, edition, version":    "db_product_edition",
                    "server sizing":                "db_server_sizing",
                    "requirements":                 "db_os_requirements",
                    "size and growth":              "db_size_and_growth",
                    "scalability":                  "db_scalability",
                    "availability":                 "db_availability",
                    "backup":                       "db_backup",
                    "security":                     "db_security",
                }
                # Row 0 is merged header; Row 1 is sub-header "Relevant Aspects / Details"
                for row in table.rows[2:]:
                    if len(row.cells) < 2:
                        continue
                    label = row.cells[0].text.strip().lower()
                    val_cell = row.cells[1]
                    if val_cell.text.strip():
                        continue
                    for key_fragment, meta_key in _DB_MAP.items():
                        if key_fragment in label:
                            value = meta.get(meta_key) or ""
                            if value:
                                self._set_cell_text(val_cell, value)
                            break
                logger.info("doc_builder.database_tier_filled")
                continue

            # ── Application Tier ───────────────────────────────────────
            # Detected by first header = "application tier".
            if "application tier" in h0:
                _APP_TIER_MAP = {
                    "product, edition, version":        "app_product_version",
                    "server sizing":                    "app_server_sizing",
                    "hardware dependencies":            "hardware_dependencies",
                    "os and dependencies":              "app_os_dependencies",
                    "required functionality":           "app_required_features",
                }
                for row in table.rows[2:]:
                    if len(row.cells) < 2:
                        continue
                    label = row.cells[0].text.strip().lower()
                    val_cell = row.cells[1]
                    if val_cell.text.strip():
                        continue
                    for key_fragment, meta_key in _APP_TIER_MAP.items():
                        if key_fragment in label:
                            value = meta.get(meta_key) or ""
                            if value:
                                self._set_cell_text(val_cell, value)
                            break
                logger.info("doc_builder.app_tier_filled")
                continue

            # ── OCI Service Sizing / Bill of Materials ─────────────────
            # Detected by first header = "oci service name".
            if "oci service name" in h0:
                bom: list[dict] = meta.get("oci_bom") or []
                if bom:
                    # Overwrite placeholder rows first, then add more if needed
                    placeholder_rows = [
                        r for r in table.rows[1:]
                        if r.cells[0].text.strip().lower().startswith("service")
                    ]
                    for ri, entry in enumerate(bom):
                        if ri < len(placeholder_rows):
                            row = placeholder_rows[ri]
                        else:
                            row = table.add_row()
                        cells = row.cells
                        if len(cells) >= 1:
                            self._set_cell_text(cells[0], entry.get("service", ""))
                        if len(cells) >= 2:
                            self._set_cell_text(cells[1], entry.get("sizing_unit", ""))
                        if len(cells) >= 3:
                            self._set_cell_text(cells[2], entry.get("amount", ""))
                        if len(cells) >= 4:
                            self._set_cell_text(cells[3], entry.get("comments", ""))
                    # Blank out any remaining placeholder rows that weren't used
                    for row in placeholder_rows[len(bom):]:
                        for cell in row.cells:
                            self._set_cell_text(cell, "")
                    logger.info("doc_builder.bom_filled entries=%d", len(bom))
                # Apply visual styling regardless of whether BOM data was provided:
                # light blue header row, dark grayish-green data rows, fix header typo.
                self._style_bom_table(table)
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

    @staticmethod
    def _set_cell_fill(cell, hex_color: str) -> None:
        """Set the background fill colour of a table cell.

        Args:
            cell: python-docx ``_Cell`` object.
            hex_color: Six-character hex colour string without the ``#``
                prefix, e.g. ``"BDD7EE"`` for light blue.
        """
        tc = cell._tc
        tcPr = tc.find(qn("w:tcPr"))
        if tcPr is None:
            tcPr = OxmlElement("w:tcPr")
            tc.insert(0, tcPr)
        for existing in tcPr.findall(qn("w:shd")):
            tcPr.remove(existing)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hex_color)
        tcPr.append(shd)

    @staticmethod
    def _set_cell_text_color(cell, hex_color: str, bold: bool = False) -> None:
        """Apply text colour (and optionally bold) to every run in a table cell.

        Args:
            cell: python-docx ``_Cell`` object.
            hex_color: Six-character hex colour string, e.g. ``"FFFFFF"``
                for white or ``"1F3864"`` for dark navy.
            bold: When *True*, also sets ``<w:b/>`` on each run's rPr.
        """
        for para in cell.paragraphs:
            for run in para.runs:
                r_elem = run._r
                rPr = r_elem.find(qn("w:rPr"))
                if rPr is None:
                    rPr = OxmlElement("w:rPr")
                    r_elem.insert(0, rPr)
                for existing in rPr.findall(qn("w:color")):
                    rPr.remove(existing)
                clr = OxmlElement("w:color")
                clr.set(qn("w:val"), hex_color)
                rPr.append(clr)
                if bold and rPr.find(qn("w:b")) is None:
                    rPr.insert(0, OxmlElement("w:b"))

    @staticmethod
    def _style_bom_table(table) -> None:
        """Apply OCI BOM table visual styling.

        * Header row: light blue fill (``BDD7EE``), dark navy bold text
          (``1F3864``).  Also fixes the common "Sizing Unites" header typo.
        * Data rows: dark grayish-green fill (``2E4A35``), white text.
        """
        if not table.rows:
            return
        # ── Header row ─────────────────────────────────────────────────
        header_row = table.rows[0]
        for cell in header_row.cells:
            cell_text = cell.text.strip()
            if "sizing" in cell_text.lower() and "unit" in cell_text.lower():
                DocumentBuilder._set_cell_text(cell, "Sizing Units (ex. vCPUs)")
            DocumentBuilder._set_cell_fill(cell, "BDD7EE")
            DocumentBuilder._set_cell_text_color(cell, "1F3864", bold=True)
        # ── Data rows ──────────────────────────────────────────────────
        for row in table.rows[1:]:
            for cell in row.cells:
                DocumentBuilder._set_cell_fill(cell, "2E4A35")
                DocumentBuilder._set_cell_text_color(cell, "FFFFFF")

    def _inject_section(self, doc: Document, section_name: str, content: str) -> bool:
        """Find heading in template, remove placeholder paragraphs, inject content."""
        raw_keyword = SECTION_HEADING_KEYWORDS.get(section_name.upper(), section_name.lower())

        # A keyword prefixed with "=" requires an EXACT heading-text match
        # (case-insensitive) rather than a substring search.  Used for short
        # keywords like "scope" that would otherwise match longer headings.
        exact_match = raw_keyword.startswith("=")
        keyword = raw_keyword[1:] if exact_match else raw_keyword

        paragraphs = list(doc.paragraphs)

        # Find the matching heading paragraph (body-level paragraphs only)
        heading_idx: int | None = None
        heading_level = 1
        for i, para in enumerate(paragraphs):
            if not self._is_heading_style(para):
                continue
            para_text = para.text.strip().lower()
            if exact_match:
                matched = (para_text == keyword.lower())
            else:
                matched = (keyword.lower() in para_text)
            if matched:
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

        # ── SCOPE special handling ───────────────────────────────────────
        # Retains the colored scope-box paragraphs from the template, removes
        # only LLM-generated intro text, and injects content inside each box.
        if section_name.upper() == "SCOPE":
            self._inject_scope_boxes(doc, heading_idx, next_heading_idx, content)
            logger.info(
                "doc_builder.section_injected section=%s blocks=%d (scope_boxes)",
                section_name, len(content_blocks),
            )
            return True

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
            _use_formatted    = section_name.upper() in _LABELED_FORMAT_SECTIONS
            _use_hierarchical = section_name.upper() in _HIERARCHICAL_BULLET_SECTIONS
            if _use_formatted:
                self._inject_formatted_blocks_after_element(anchor_elem, content)
            elif _use_hierarchical:
                self._inject_hierarchical_bullets_after_element(anchor_elem, content)
            else:
                self._inject_blocks_after_element(anchor_elem, content)
            logger.info(
                "doc_builder.section_injected section=%s blocks=%d (full_clear%s%s)",
                section_name, len(content_blocks),
                ",formatted" if _use_formatted else "",
                ",hierarchical" if _use_hierarchical else "",
            )
            return True

        # ── Normal sections ──────────────────────────────────────────────
        # Remove placeholder paragraphs (but not sub-headings or real content)
        for i in range(heading_idx + 1, next_heading_idx):
            para = paragraphs[i]
            if _PLACEHOLDER_RE.search(para.text):
                body.remove(para._element)

        # Determine the injection anchor.
        # Advance the anchor past any template intro text so LLM content is
        # appended after it (never before surviving template paragraphs).
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
        """Fallback: append section at end of document.

        For sections in :data:`_LABELED_FORMAT_SECTIONS` (e.g. ARCHITECT REVIEW),
        the formatted bold-label + bullet injection is used so that sub-topic
        structure is preserved even when the section is not found in the template.
        """
        heading = doc.add_heading(section_name.title(), level=1)
        if section_name.upper() in _LABELED_FORMAT_SECTIONS:
            self._inject_formatted_blocks_after_element(heading._element, content)
        else:
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

        Inserts in reverse order so that block[0] ends up immediately after
        anchor_elem.  Uses :func:`_build_para_elem` so that any
        ``PENDING TO REVIEW`` markers within the text are rendered in red.
        """
        content_blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
        for block in reversed(content_blocks):
            anchor_elem.addnext(_build_para_elem(block))

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
