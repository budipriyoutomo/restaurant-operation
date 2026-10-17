"""Built-in daily ops templates per outlet type (Todo-Ops-Checklist Fase 4, UX-11).

A new company picks its outlet type during onboarding and gets ready-made
templates (and, for a chosen outlet, schedules) instead of an empty editor.
Temperature items carry generic limits and no asset: bind them to the outlet's
monitored chillers and freezers in the template editor so readings reach the
temperature log.

Plain data, validated by `preset_problems` (unit-tested); applied by
ops_preset_service and scripts/seed_ops_templates.py.
"""

from __future__ import annotations

from datetime import time
from typing import Dict, List

from app.services.ops_checklist_rules import FREQUENCIES, ITEM_TYPES, SHIFTS


def _check(section: str, title: str, *, photo: bool = False, critical: bool = False) -> dict:
    return {"section": section, "title": title, "itemType": "check", "requiresPhoto": photo, "isCritical": critical}


def _temp(section: str, title: str, lo: float, hi: float) -> dict:
    return {"section": section, "title": title, "itemType": "temperature", "minValue": lo, "maxValue": hi,
            "unit": "°C", "isCritical": True}


# ── Shared building blocks ───────────────────────────────────────────────────

_HYGIENE = [
    _check("Kebersihan", "Sabun & tisu tersedia di wastafel cuci tangan"),
    _check("Kebersihan", "Lantai dapur kering dan bersih"),
    _check("Staff", "Seragam, hairnet & sarung tangan dipakai"),
]
_SAFETY = [
    _check("Keamanan", "Gas, listrik & APAR aman", critical=True),
]
_COLD = [
    _temp("Suhu", "Suhu chiller", 0, 5),
    _temp("Suhu", "Suhu freezer", -22, -15),
]
_CLOSING_KITCHEN = [
    _check("Dapur", "Kompor & gas dimatikan", critical=True),
    _check("Dapur", "Bahan sisa diberi label tanggal & disimpan"),
    _check("Dapur", "Chiller & freezer tertutup rapat"),
    _check("Kebersihan", "Sampah dibuang, tempat sampah dicuci"),
    _check("Kebersihan", "Lantai dibersihkan"),
]
_DEEP_CLEAN = {
    "name": "Deep Cleaning Mingguan", "shift": "other", "frequency": "weekly", "daysOfWeek": [1],
    "dueTime": "15:00", "openMinutesBefore": 2 * 1440, "graceMinutes": 1440,
    "description": "Pembersihan menyeluruh sekali seminggu.",
    "items": [
        _check("Dapur", "Hood & filter exhaust dibersihkan", photo=True),
        _check("Dapur", "Bagian dalam chiller & freezer dibersihkan", photo=True),
        _check("Dapur", "Saluran air & floor drain dibersihkan"),
        _check("Gudang", "Rak gudang kering dirapikan, FIFO dicek"),
    ],
}
_CALIBRATION = {
    "name": "Kalibrasi Termometer Bulanan", "shift": "other", "frequency": "monthly", "dayOfMonth": 1,
    "dueTime": "14:00", "openMinutesBefore": 3 * 1440, "graceMinutes": 1440,
    "description": "Uji termometer di air es (0 °C) sebulan sekali.",
    "items": [
        {"section": "Kalibrasi", "title": "Termometer probe di air es", "itemType": "number",
         "minValue": -1, "maxValue": 1, "unit": "°C", "isCritical": True},
        _check("Kalibrasi", "Termometer yang meleset diganti / ditandai"),
    ],
}


def _temperature_log(due: str) -> dict:
    return {"name": f"Log Suhu {due}", "shift": "other", "dueTime": due, "openMinutesBefore": 60, "graceMinutes": 60,
            "description": "Catat suhu semua chiller & freezer.", "items": list(_COLD)}


PRESETS: Dict[str, dict] = {
    "cafe": {
        "label": "Cafe",
        "templates": [
            {"name": "Opening Checklist", "shift": "opening", "dueTime": "07:30", "description": "",
             "items": [*_COLD, *_HYGIENE,
                       _check("Bar", "Mesin espresso dibersihkan & dikalibrasi"),
                       _check("Bar", "Susu & bahan segar dicek tanggal kedaluwarsa (FIFO)"),
                       _check("Area tamu", "Area tamu siap", photo=True),
                       _check("Area tamu", "Toilet tamu bersih", photo=True), *_SAFETY]},
            {"name": "Closing Checklist", "shift": "closing", "dueTime": "22:00", "description": "",
             "items": [_check("Bar", "Mesin espresso & grinder dibersihkan, backflush"),
                       *_CLOSING_KITCHEN, _check("Kasir", "Kas ditutup & dicocokkan")]},
            _temperature_log("14:00"),
            _DEEP_CLEAN,
        ],
    },
    "resto": {
        "label": "Restoran",
        "templates": [
            {"name": "Opening Checklist", "shift": "opening", "dueTime": "09:30", "description": "",
             "items": [*_COLD, *_HYGIENE,
                       _check("Dapur", "Tanggal kedaluwarsa bahan dicek (FIFO)"),
                       _check("Area tamu", "Area tamu siap", photo=True),
                       _check("Area tamu", "Toilet tamu bersih", photo=True), *_SAFETY]},
            {"name": "Midday Checklist", "shift": "midday", "dueTime": "15:00", "description": "",
             "items": [*_COLD, _check("Dapur", "Makanan panas di holding ≥ 60 °C"),
                       _check("Area tamu", "Toilet tamu bersih", photo=True)]},
            {"name": "Closing Checklist", "shift": "closing", "dueTime": "22:30", "description": "",
             "items": [*_CLOSING_KITCHEN, _check("Kasir", "Kas ditutup & dicocokkan")]},
            _temperature_log("19:00"),
            _DEEP_CLEAN,
            _CALIBRATION,
        ],
    },
    "cloud_kitchen": {
        "label": "Cloud kitchen",
        "templates": [
            {"name": "Opening Checklist", "shift": "opening", "dueTime": "09:00", "description": "",
             "items": [*_COLD, *_HYGIENE,
                       _check("Dapur", "Tanggal kedaluwarsa bahan dicek (FIFO)"),
                       _check("Packing", "Kemasan, segel & label pesanan tersedia"),
                       _check("Packing", "Area pickup ojol bersih & rapi", photo=True), *_SAFETY]},
            {"name": "Closing Checklist", "shift": "closing", "dueTime": "23:00", "description": "",
             "items": [*_CLOSING_KITCHEN, _check("Packing", "Sisa kemasan dirapikan, stok dicatat")]},
            _temperature_log("15:00"),
            _DEEP_CLEAN,
            _CALIBRATION,
        ],
    },
}


def preset_problems(preset: dict) -> List[str]:
    """What is wrong with a preset (empty = valid)."""
    problems: List[str] = []
    if not preset.get("label"):
        problems.append("label missing")
    names = [t.get("name", "") for t in preset.get("templates", [])]
    if len(set(n.lower() for n in names)) != len(names):
        problems.append("duplicate template names")
    for t in preset.get("templates", []):
        name = t.get("name") or "?"
        if not t.get("name"):
            problems.append("template without a name")
        if t.get("shift") not in SHIFTS:
            problems.append(f"{name}: unknown shift {t.get('shift')!r}")
        if t.get("frequency", "daily") not in FREQUENCIES:
            problems.append(f"{name}: unknown frequency")
        try:
            time.fromisoformat(t.get("dueTime", ""))
        except (TypeError, ValueError):
            problems.append(f"{name}: dueTime must be HH:MM")
        if not t.get("items"):
            problems.append(f"{name}: no items")
        for i in t.get("items", []):
            if not i.get("title") or i.get("itemType") not in ITEM_TYPES:
                problems.append(f"{name}: invalid item {i.get('title')!r}")
            if i.get("itemType") in ("number", "temperature") and i.get("minValue") is None and i.get("maxValue") is None:
                problems.append(f"{name}: {i.get('title')} needs limits")
    return problems
