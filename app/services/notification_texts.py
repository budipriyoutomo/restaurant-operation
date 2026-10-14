"""Notification language (Todo-Pilot §16 / UX-3).

Notifications are written in Indonesian by default, in the same glossary as the
UI (Issue = Laporan, Task = Tugas, Work Order = Perintah Kerja, Approval =
Persetujuan). Each text carries both languages; every channel (in-app, email,
WhatsApp) picks the one the *recipient* chose in Settings
(users.preferences.language). Status labels mirror frontend lib/i18n.
"""

from __future__ import annotations

from typing import Dict, Union

DEFAULT_LANGUAGE = "id"
LANGUAGES = ("id", "en")

Localized = Dict[str, str]          # {"id": "...", "en": "..."}
Text = Union[str, Localized]


def L(id_text: str, en_text: str) -> Localized:
    return {"id": id_text, "en": en_text}


def recipient_language(user) -> str:
    lang = ((getattr(user, "preferences", None) or {}).get("language"))
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def pick(text: Text, user) -> str:
    """The text in the recipient's language (plain strings pass through)."""
    if isinstance(text, dict):
        return text.get(recipient_language(user)) or text[DEFAULT_LANGUAGE]
    return text


_STATUS = {
    # Issue / Task
    "open":        ("Terbuka", "Open"),
    "assigned":    ("Ditugaskan", "Assigned"),
    "in-progress": ("Dikerjakan", "In progress"),
    "waiting":     ("Menunggu", "Waiting"),
    "resolved":    ("Selesai", "Resolved"),
    "closed":      ("Ditutup", "Closed"),
    "cancelled":   ("Dibatalkan", "Cancelled"),
    # Approval
    "pending":     ("Menunggu", "Pending"),
    "approved":    ("Disetujui", "Approved"),
    "rejected":    ("Ditolak", "Rejected"),
}


def status_label(status: str, language: str) -> str:
    labels = _STATUS.get(status)
    if not labels:
        return status
    return labels[0] if language == "id" else labels[1]


def status_text(status: str) -> Localized:
    return L(status_label(status, "id"), status_label(status, "en"))
