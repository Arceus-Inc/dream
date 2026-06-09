"""Tests for the shared task_id / sprint_number validators.

Covers the filename-safety contract these helpers exist to enforce:
``checked_task_id`` must reject anything that could escape or alias the
intended path (slashes, backslashes, NUL, ``..``, drive-colon / NTFS
alternate-data-stream syntax), and ``checked_sprint_number`` must accept
only real positive integers (not floats, not booleans).
"""

from __future__ import annotations

import pytest

from dream.sprint._checks import checked_sprint_number, checked_task_id

# --- checked_task_id ---------------------------------------------------


@pytest.mark.parametrize("good", ["t1", "feature-x", "abc_123", "PR-59"])
def test_checked_task_id_accepts_safe_ids(good: str) -> None:
    assert checked_task_id(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        ".",
        "..",
        "a/b",
        "a\\b",
        "a\x00b",
        "name:stream",  # NTFS alternate data stream / Windows drive colon
        "C:",
    ],
)
def test_checked_task_id_rejects_unsafe_ids(bad: str) -> None:
    with pytest.raises(ValueError, match="unsafe task_id"):
        checked_task_id(bad)


# --- checked_sprint_number --------------------------------------------


@pytest.mark.parametrize("n", [1, 2, 100])
def test_checked_sprint_number_accepts_positive_ints(n: int) -> None:
    assert checked_sprint_number(n) == n


@pytest.mark.parametrize("n", [0, -1, -100])
def test_checked_sprint_number_rejects_below_one(n: int) -> None:
    with pytest.raises(ValueError, match=">= 1"):
        checked_sprint_number(n)


def test_checked_sprint_number_rejects_float() -> None:
    with pytest.raises(TypeError, match="int"):
        checked_sprint_number(1.5)  # type: ignore[arg-type]


def test_checked_sprint_number_rejects_bool() -> None:
    # ``True == 1`` would otherwise sneak through the >= 1 check and produce
    # a ``sprint-True.json`` filename.
    with pytest.raises(TypeError, match="int"):
        checked_sprint_number(True)  # type: ignore[arg-type]
