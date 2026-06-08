"""Spec 13F.1 — governance standing-order extraction + rendering.

Deterministic (no paraphrase) extraction of the ALWAYS / NEVER lists from
``core-beliefs.md``; a missing file or section warns but never blocks.
"""

from __future__ import annotations

from pathlib import Path

from dream.services.core_beliefs import (
    StandingOrders,
    extract_standing_orders,
    render_standing_orders,
)

CORE = """\
# Core beliefs

## What we do
We build a harness.

## What we don't do
- never delete user data without confirmation
- never push to main directly

## How we make decisions
We argue, then commit.

## Standing orders
- always run tests before committing
- always write a docstring
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "core-beliefs.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_extracts_always_and_never(tmp_path: Path) -> None:
    orders = extract_standing_orders(_write(tmp_path, CORE))
    assert orders.always == (
        "always run tests before committing",
        "always write a docstring",
    )
    assert orders.never == (
        "never delete user data without confirmation",
        "never push to main directly",
    )
    assert orders.warnings == ()


def test_headings_are_case_insensitive(tmp_path: Path) -> None:
    orders = extract_standing_orders(_write(tmp_path, "## STANDING ORDERS\n- a\n## What We Don't Do\n- b\n"))
    assert orders.always == ("a",)
    assert orders.never == ("b",)


def test_missing_standing_orders_section_warns(tmp_path: Path) -> None:
    orders = extract_standing_orders(_write(tmp_path, "## What we don't do\n- b\n"))
    assert orders.always == ()
    assert orders.never == ("b",)
    assert any("standing orders" in w.lower() for w in orders.warnings)


def test_missing_file_is_empty_with_warning(tmp_path: Path) -> None:
    orders = extract_standing_orders(tmp_path / "absent.md")
    assert orders.always == ()
    assert orders.never == ()
    assert orders.warnings


def test_non_utf8_file_does_not_crash(tmp_path: Path) -> None:
    # A core-beliefs.md with invalid UTF-8 bytes must not crash session start.
    p = tmp_path / "core-beliefs.md"
    p.write_bytes(b"## Standing orders\n- always \xff run tests\n")
    orders = extract_standing_orders(p)  # must not raise
    assert orders.always  # decoded leniently, the bullet still extracted


def test_non_bullet_lines_ignored(tmp_path: Path) -> None:
    orders = extract_standing_orders(_write(tmp_path, "## Standing orders\nSome prose, not a bullet.\n- a\n"))
    assert orders.always == ("a",)


def test_section_ends_at_next_heading(tmp_path: Path) -> None:
    orders = extract_standing_orders(_write(tmp_path, "## Standing orders\n- a\n## Other\n- not included\n"))
    assert orders.always == ("a",)


def test_star_bullets_supported(tmp_path: Path) -> None:
    orders = extract_standing_orders(_write(tmp_path, "## Standing orders\n* a\n* b\n"))
    assert orders.always == ("a", "b")


def test_render_includes_both_lists() -> None:
    block = render_standing_orders(StandingOrders(always=("run tests",), never=("delete data",)))
    assert "ALWAYS:" in block
    assert "- run tests" in block
    assert "NEVER:" in block
    assert "- delete data" in block
    assert "core-beliefs.md" in block


def test_render_empty_when_no_orders() -> None:
    assert render_standing_orders(StandingOrders()) == ""


def test_render_only_always_omits_never_header() -> None:
    block = render_standing_orders(StandingOrders(always=("a",)))
    assert "ALWAYS:" in block
    assert "NEVER:" not in block
