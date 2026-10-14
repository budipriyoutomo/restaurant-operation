"""Unit tests — notification language (Todo-Pilot §16 / UX-3). No database.

Notifications are written in Indonesian by default; a recipient who chose
English in Settings (users.preferences.language) gets English.
"""

from types import SimpleNamespace as NS

import pytest

from app.services.notification_texts import L, pick, recipient_language, status_label


def test_default_language_is_indonesian():
    assert recipient_language(NS(preferences=None)) == "id"
    assert recipient_language(NS(preferences={})) == "id"
    assert recipient_language(NS(preferences={"language": "fr"})) == "id"


def test_english_when_the_user_chose_it():
    assert recipient_language(NS(preferences={"language": "en"})) == "en"


def test_pick_localised_text_per_recipient():
    text = L("Laporan baru", "New issue")
    assert pick(text, NS(preferences={})) == "Laporan baru"
    assert pick(text, NS(preferences={"language": "en"})) == "New issue"


def test_plain_strings_pass_through():
    assert pick("as is", NS(preferences={"language": "en"})) == "as is"


@pytest.mark.parametrize("status,id_label,en_label", [
    ("in-progress", "Dikerjakan", "In progress"),
    ("resolved", "Selesai", "Resolved"),
    ("approved", "Disetujui", "Approved"),
    ("mystery", "mystery", "mystery"),
])
def test_status_labels_match_the_ui_glossary(status, id_label, en_label):
    assert status_label(status, "id") == id_label
    assert status_label(status, "en") == en_label
