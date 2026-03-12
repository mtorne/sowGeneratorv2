"""Style constants extracted from HUG_Labs_SoW_v0.5.docx (single source of truth).

ALL formatting values in doc_builder.py MUST reference this module.
NEVER hardcode font names, sizes, colors, or layout values elsewhere.

Extraction methodology
----------------------
Values were extracted programmatically from the reference DOCX via:
  - word/styles.xml  → paragraph/run/table styles
  - word/theme/theme1.xml → theme font resolution (minorHAnsi → Oracle Sans)
  - word/numbering.xml → list abstractNum definitions
  - word/document.xml → actual table widths, column proportions, shading
  - word/settings.xml → page size and margins (via sectPr)

Regenerate by running:
  python scripts/extract_ref_styles.py HUG_Labs_SoW_v0.5.docx
"""

from __future__ import annotations

# ── Typography ────────────────────────────────────────────────────────────────

# Body font: "Oracle Sans" — theme minorHAnsi as declared in word/theme/theme1.xml.
# Fallback: Calibri (Word default minor font) if Oracle Sans is not installed on
# the reader's machine. Word handles this gracefully via theme font substitution.
BODY_FONT = "Oracle Sans"

# Body text size: sz=18 in half-points = 9 pt (from w:docDefaults in styles.xml).
BODY_FONT_SIZE_HPC = 18  # half-points

# Primary body text colour (from w:docDefaults w:color).
BODY_COLOR = "312D2A"  # dark warm-black

# Monospace font: used for IPs, CIDRs, config values, version strings.
# Present in PlainText, MacroText, and Code styles in the reference.
MONO_FONT = "Consolas"

# ── Heading typography ────────────────────────────────────────────────────────
# All sizes in half-points (sz value). 1 pt = 2 half-points.

# Heading 1 (from Heading1 style in styles.xml)
H1_FONT_SIZE_HPC = 26   # 13 pt
H1_COLOR = "AE562C"     # Oracle orange
H1_BOLD = True
H1_SPACE_BEFORE = 240   # twips (before paragraph)
H1_SPACE_AFTER = 80     # twips

# Heading 2
H2_FONT_SIZE_HPC = 26   # 13 pt
H2_COLOR = None          # inherits body colour (312D2A)
H2_BOLD = True
H2_SPACE_BEFORE = 240
H2_SPACE_AFTER = 80
H2_LINE_SPACING = 320   # twips (line spacing)

# Heading 3
H3_FONT_SIZE_HPC = 24   # 12 pt
H3_COLOR = None          # inherits body colour
H3_BOLD = True
H3_SPACE_BEFORE = 200
H3_SPACE_AFTER = 80
H3_LINE_SPACING = 320

# ── Color palette ─────────────────────────────────────────────────────────────
# All hex values without '#'.

# Table header fills
TABLE_HEADER_FILL = "AE562C"   # Oracle orange — BasicTable02Redwood firstRow
TABLE_HEADER_TEXT = "FCFBFA"   # near-white — header text
BOM_HEADER_FILL = "001F5B"     # dark navy — OCI Service Sizing / Acceptance table header

# Table cell fills
TABLE_CELL_FILL = "F1EFED"        # light cream — default cell background
TABLE_ALT_FILL_DARK = "D4DFDF"   # teal-grey — first column (Service Name etc.)
TABLE_ALT_FILL_LIGHT = "E9EFEF"  # lighter teal-grey — data columns
TABLE_SECTION_HEADER_FILL = "98D2B0"   # medium green — sub-section tier headers
TABLE_GREEN_LIGHT_FILL = "CBE8D7"      # light green — general aspects rows

# Responsibility shading (scope / RACI / managed services tables)
CUSTOMER_COLOR = "E9EFEF"   # matches TABLE_ALT_FILL_LIGHT
ORACLE_COLOR = "953427"     # dark red — Oracle-owned table headers (participants, revision)
ORACLE_LIGHT_COLOR = "FFEBE1"  # light orange — Oracle-specific data cells

# Error / pending markers
PENDING_COLOR = "FF0000"    # red — PENDING TO REVIEW text

# ── Table borders ─────────────────────────────────────────────────────────────

# Outer (table perimeter) border — used on BOM and acceptance criteria tables
OUTER_BORDER_COLOR = "312D2A"  # dark warm-black
OUTER_BORDER_SIZE = 6          # sz=6 → 0.75 pt

# Inner (between-cell) border in BasicTable01/02Redwood
# Near-white so cells appear seamless; alternating row shading creates visual
# separation instead of explicit rules.
INNER_BORDER_COLOR = "FCFBFA"  # near-white (visually invisible)
INNER_BORDER_SIZE = 8          # sz=8 → 1 pt

# ── Cell spacing ──────────────────────────────────────────────────────────────
# From w:tblCellMar in BasicTable01Redwood (base style for both table types).

CELL_MARGIN_TOP = 72     # dxa
CELL_MARGIN_LEFT = 72    # dxa
CELL_MARGIN_BOTTOM = 72  # dxa
CELL_MARGIN_RIGHT = 72   # dxa

# ── Page layout ───────────────────────────────────────────────────────────────
# From w:pgSz / w:pgMar in the first sectPr of document.xml.
# All values in dxa (1440 dxa = 1 inch).

PAGE_WIDTH = 12240    # 8.5 inches — US Letter
PAGE_HEIGHT = 15840   # 11 inches
MARGIN_TOP = 1080     # 0.75 inch
MARGIN_RIGHT = 1080   # 0.75 inch
MARGIN_BOTTOM = 1080  # 0.75 inch
MARGIN_LEFT = 1080    # 0.75 inch

# Derived: printable text area width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
CONTENT_WIDTH_DXA = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT  # 10080 dxa ≈ 7 inches

# ── Column proportions (% of CONTENT_WIDTH_DXA) ──────────────────────────────
# Proportions measured from reference tables; multiply by CONTENT_WIDTH_DXA
# to get absolute dxa widths.  Rounded to nearest integer.

# OCI Service Sizing table (Table 13 in reference): 10314 dxa total (≈ CONTENT_WIDTH)
# Original widths: 2943 | 1560 | 1275 | 4536
# Desired proportions (per requirements): 40% | 20% | 15% | 25%
BOM_COL_WIDTHS_DXA = (
    int(CONTENT_WIDTH_DXA * 0.40),  # Service Name   ≈ 4032
    int(CONTENT_WIDTH_DXA * 0.20),  # Sizing/Units   ≈ 2016
    int(CONTENT_WIDTH_DXA * 0.15),  # Amounts        ≈ 1512
    int(CONTENT_WIDTH_DXA * 0.25),  # Comments       ≈ 2520
)

# Participants table (Table 2): 10278 dxa
# Original widths: 1488 | 3120 | 2552 | 3118
PARTICIPANTS_COL_WIDTHS_DXA = (1488, 3120, 2552, 3118)

# Milestone table (Table 5): 10278 dxa
# Original widths: 4010 | 1622 | 1244 | 3402
MILESTONE_COL_WIDTHS_DXA = (4010, 1622, 1244, 3402)

# ── Word table style IDs ──────────────────────────────────────────────────────
# Applied via w:tblStyle in tblPr. Both derive from BasicTable01Redwood (TableGrid).

TABLE_STYLE_PRIMARY = "BasicTable02Redwood"  # orange header row — primary SoW tables
TABLE_STYLE_GREEN = "BasicTable01Redwood"    # green header row — acceptance criteria
TABLE_STYLE_BOM = "BasicTable09Redwood"      # dark navy header — OCI sizing / acceptance

# ── Word paragraph style names for lists ─────────────────────────────────────
# These style IDs exist in the reference template's styles.xml.
# Use these names when setting paragraph style via python-docx or raw XML.

# Level 1 body bullets (abstractNumId=11, ilvl=0 → \uf097 at left=720, hanging=360)
STYLE_LIST_BULLET = "NormalBodyBullet1"

# Level 2 nested bullets (same abstractNum, ilvl=1 → \uf02d dash at left=1440, hanging=360)
STYLE_LIST_BULLET2 = "NormalBodyBullet2"

# Level 3 deeply nested bullets (ilvl=2 → \uf0ae at left=2160, hanging=360)
STYLE_LIST_BULLET3 = "NormalBodyBullet3"

# Numbered steps — use for recovery procedures and sequential steps
# (abstractNumId=42, ilvl=0 → decimal at left=720, hanging=360)
STYLE_LIST_NUMBERED = "NumberedList1"

# Continuation of a list block (no bullet, same indent)
STYLE_LIST_CONTINUE = "ListContinue"

# Table-specific bullet (abstractNumId=9 → \uf097 at left=360, hanging=360)
STYLE_TABLE_BULLET = "TableBodyBulletList"
STYLE_TABLE_NUMBERED = "TableBodyNumberedList"

# ── Heading style IDs ─────────────────────────────────────────────────────────
STYLE_HEADING1 = "Heading1"
STYLE_HEADING2 = "Heading2"
STYLE_HEADING3 = "Heading3"
STYLE_HEADING4 = "Heading4"

# ── Spacing ───────────────────────────────────────────────────────────────────
# Default paragraph spacing after (from w:pPrDefault in styles.xml docDefaults).
DEFAULT_SPACE_AFTER = 120  # twips
