"""Spec 10 slice A — bundled default role manifests.

Pins the *content* of the three canonical roles: planner is read-only (no
writers, no shell), evaluator is read-only + may run named verifiers, and
generator is the only role allowed to use ``tools: None`` (= "all,
intersected with the sandbox tier at #13").
"""

from __future__ import annotations

import pytest

from dream.roles import default_role_manifest

# --- the names ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["planner", "generator", "evaluator"])
def test_default_exists_for_each_canonical_role(name: str) -> None:
    m = default_role_manifest(name)  # type: ignore[arg-type]
    assert m.name == name


def test_default_for_unknown_role_raises() -> None:
    with pytest.raises(ValueError):
        default_role_manifest("ceo")  # type: ignore[arg-type]


# --- planner: read-only -----------------------------------------------------


def test_planner_default_tools_are_read_only_triplet() -> None:
    m = default_role_manifest("planner")
    assert m.tools is not None
    tools = set(m.tools)
    # file_read + git + (mcp/observability reads) — exact set is the contract;
    # writers must NOT appear here.
    assert "file_read" in tools
    assert "git" in tools
    assert "file_write" not in tools
    assert "file_edit" not in tools
    assert "bash" not in tools


def test_planner_default_disallowed_tools_lists_all_writers() -> None:
    m = default_role_manifest("planner")
    disallowed = set(m.disallowed_tools)
    assert {"file_write", "file_edit", "bash"} <= disallowed


def test_planner_default_permission_mode_is_plan() -> None:
    # Plan mode is the natural fit: planner produces artefacts, never side effects.
    m = default_role_manifest("planner")
    assert m.permission_mode == "plan"


# --- generator: tools=None (all, tier-intersected) ---------------------------


def test_generator_default_tools_is_null_meaning_all() -> None:
    m = default_role_manifest("generator")
    assert m.tools is None


def test_generator_default_permission_mode_is_default() -> None:
    m = default_role_manifest("generator")
    assert m.permission_mode == "default"


# --- evaluator: read-only triplet ------------------------------------------


def test_evaluator_default_tools_are_read_only_plus_no_writers() -> None:
    m = default_role_manifest("evaluator")
    assert m.tools is not None
    tools = set(m.tools)
    assert "file_read" in tools
    assert "file_write" not in tools
    assert "file_edit" not in tools
    assert "bash" not in tools


def test_evaluator_default_disallowed_lists_writers() -> None:
    m = default_role_manifest("evaluator")
    assert {"file_write", "file_edit", "bash"} <= set(m.disallowed_tools)
