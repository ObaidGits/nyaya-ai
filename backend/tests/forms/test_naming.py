"""Naming convention tests (REQUIREMENTS B-011..B-015; ARCHITECTURE §27)."""

from __future__ import annotations

from app.forms.naming import ensure_unique, form_filename, slugify_title


def test_filename_follows_required_convention() -> None:
    assert (
        form_filename(12, "Bond and Bail-Bond for Attendance before Court")
        == "FORM-12_Bond-and-Bail-Bond-for-Attendance-Before-Court.pdf"
    )


def test_filenames_have_no_spaces_and_are_filesystem_safe() -> None:
    name = form_filename(4, 'BOND AND BAIL-BOND AFTER ARREST: "QUOTED"/SLASH?')
    assert " " not in name
    assert "/" not in name and ":" not in name and '"' not in name
    assert name.startswith("FORM-4_")
    assert name.endswith(".pdf")


def test_slug_is_deterministic() -> None:
    assert slugify_title("WARRANT OF ARREST") == slugify_title("WARRANT OF ARREST")
    assert slugify_title("WARRANT  OF   ARREST") == "Warrant-of-Arrest"


def test_degenerate_title_still_produces_safe_slug() -> None:
    assert form_filename(7, "///..") == "FORM-7_Untitled.pdf"


def test_collisions_are_disambiguated() -> None:
    first = "FORM-5_Bond.pdf"
    second = ensure_unique(first, {first})
    third = ensure_unique(first, {first, second})
    assert {first, second, third} == {"FORM-5_Bond.pdf", "FORM-5_Bond-2.pdf", "FORM-5_Bond-3.pdf"}
