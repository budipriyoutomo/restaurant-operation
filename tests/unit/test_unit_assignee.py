"""Unit tests — assignee name matching (Todo-Pilot §17 / UX-4). No database.

Legacy free-text assignees are linked to a user only when exactly one active
user has that name; a guess would page the wrong person.
"""

from types import SimpleNamespace as NS

from app.services.assignee_service import UNASSIGNED, is_unassigned, match_user_by_name

USERS = [
    NS(id="u1", name="Budi Santoso", is_active=True),
    NS(id="u2", name="Siti", is_active=True),
    NS(id="u3", name="Siti", is_active=True),           # duplicate name
    NS(id="u4", name="Andi", is_active=False),          # inactive
]


def test_unique_active_name_matches_case_and_space_insensitively():
    assert match_user_by_name(USERS, "  budi santoso ") == "u1"


def test_duplicate_name_is_ambiguous():
    assert match_user_by_name(USERS, "Siti") is None


def test_inactive_user_never_matches():
    assert match_user_by_name(USERS, "Andi") is None


def test_unknown_or_empty_name():
    assert match_user_by_name(USERS, "Joko") is None
    assert match_user_by_name(USERS, "") is None
    assert match_user_by_name(USERS, None) is None
    assert match_user_by_name(USERS, UNASSIGNED) is None


def test_is_unassigned():
    assert is_unassigned(None) and is_unassigned("") and is_unassigned(" unassigned ")
    assert not is_unassigned("Budi")
