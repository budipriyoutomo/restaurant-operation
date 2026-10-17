"""Inspection export — pure helpers (Todo-Ops-Checklist Fase 4). No database.

The PDF uses fpdf2's core fonts (no font files to ship), which draw latin-1
only. Typographic characters are mapped to their plain look-alikes; anything
else (emoji, other scripts) becomes "?" rather than failing the export.
"""

from __future__ import annotations

from typing import Optional

_REPLACE = {
    "−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-",
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
    "…": "...", "•": "-", " ": " ", " ": " ", " ": " ",
    "×": "x",
}


def pdf_text(value: Optional[object]) -> str:
    if value is None:
        return ""
    out = []
    for ch in str(value):
        ch = _REPLACE.get(ch, ch)
        out.append(ch if all(ord(c) < 256 for c in ch) else "?")
    return "".join(out)
