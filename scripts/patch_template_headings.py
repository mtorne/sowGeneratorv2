#!/usr/bin/env python3
"""One-time script to insert missing Heading 1 paragraphs into sow_template.docx.

HIGH AVAILABILITY and MANAGED SERVICES CONFIGURATION are absent from the
template, causing doc_builder to fall back to appending them at the end of
the document instead of injecting content in-place.

This script inserts both headings immediately before the "Closing Feedback"
heading so that doc_builder can find them via its keyword search.

Usage (run from repo root on the server):
    python scripts/patch_template_headings.py [path/to/sow_template.docx]

The file is modified in-place (a .bak backup is written first).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _make_heading_element(doc: Document, text: str, level: int = 1):
    """Create a <w:p> element styled as Heading <level> with the given text."""
    para = doc.add_paragraph(style=f"Heading {level}")
    para.text = text
    # Detach from the end of the document body — we'll re-insert it manually.
    para._element.getparent().remove(para._element)
    return para._element


def patch(template_path: Path) -> None:
    backup_path = template_path.with_suffix(".docx.bak")
    shutil.copy2(template_path, backup_path)
    print(f"Backup written to: {backup_path}")

    doc = Document(str(template_path))

    # Find the "Closing Feedback" heading paragraph element.
    anchor = None
    for para in doc.paragraphs:
        style = para.style
        is_heading = False
        while style is not None:
            if style.name.startswith("Heading"):
                is_heading = True
                break
            style = getattr(style, "base_style", None)
        if is_heading and "closing feedback" in para.text.lower():
            anchor = para._element
            break

    if anchor is None:
        print("ERROR: Could not find 'Closing Feedback' heading in template.")
        print("Available headings:")
        for para in doc.paragraphs:
            style = para.style
            while style is not None:
                if style.name.startswith("Heading"):
                    print(f"  [{style.name}] {para.text!r}")
                    break
                style = getattr(style, "base_style", None)
        sys.exit(1)

    # Build the two missing heading elements (inserted in reverse order so
    # HIGH AVAILABILITY ends up immediately before MANAGED SERVICES CONFIGURATION).
    for heading_text in reversed([
        "High Availability",
        "Managed Services Configuration",
    ]):
        elem = _make_heading_element(doc, heading_text, level=1)
        anchor.addprevious(elem)
        print(f"Inserted heading: '{heading_text}' (before 'Closing Feedback')")

    doc.save(str(template_path))
    print(f"Template patched and saved: {template_path}")

    # Verify
    print("\nVerification — headings near end of template:")
    found = []
    for para in doc.paragraphs:
        style = para.style
        while style is not None:
            if style.name.startswith("Heading"):
                found.append(para.text)
                break
            style = getattr(style, "base_style", None)
    for h in found[-6:]:
        print(f"  {h!r}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        # Default: repo-relative path used in production
        path = Path(__file__).resolve().parent.parent / "app" / "sow_template.docx"

    if not path.exists():
        print(f"ERROR: Template not found at {path}")
        sys.exit(1)

    patch(path)
