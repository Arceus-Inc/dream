"""Spec 10 slice A — bundled default role manifests.

Pins the *content* of the three canonical roles: planner is read-only (no
writers, no shell), evaluator has reads + bash for in-session verify, and
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
    # read_file + git + (mcp/observability reads) — exact set is the contract;
    # writers must NOT appear here.
    assert "read_file" in tools
    assert "git" in tools
    assert "write_file" not in tools
    assert "apply_patch" not in tools
    assert "bash" not in tools


def test_planner_default_disallowed_tools_lists_all_writers() -> None:
    m = default_role_manifest("planner")
    disallowed = set(m.disallowed_tools)
    assert {"write_file", "apply_patch", "bash"} <= disallowed


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


# --- evaluator: reads + bash verify; no writers / spawn ---------------------


def test_evaluator_default_tools_include_bash_for_in_session_verify() -> None:
    m = default_role_manifest("evaluator")
    assert m.tools is not None
    tools = set(m.tools)
    assert "read_file" in tools
    assert "bash" in tools
    assert "write_file" not in tools
    assert "apply_patch" not in tools
    assert "spawn_subagent" not in tools


def test_evaluator_default_disallowed_lists_writers_not_bash() -> None:
    m = default_role_manifest("evaluator")
    disallowed = set(m.disallowed_tools)
    assert {"write_file", "apply_patch"} <= disallowed
    assert "bash" not in disallowed


def test_evaluator_default_permission_mode_allows_tool_execution() -> None:
    # plan mode is for the planner; evaluator must execute bash verify steps.
    m = default_role_manifest("evaluator")
    assert m.permission_mode == "default"


# --- registry alignment (names must match real registered tools) ------------


def test_default_manifest_tool_names_exist_in_default_registry() -> None:
    # Regression: the read-only triplet referenced ``file_read`` while the
    # registered tool is ``read_file``; ``compute_minimum_toolset`` silently
    # drops unknown names, so planner/evaluator lost file reading entirely and
    # collapsed to ``{git, query_logs}``. Every name a default manifest lists
    # (tools + disallowed_tools) must be a real registered tool name.
    from dream.roles import compute_minimum_toolset
    from dream.tools.builtin import default_registry

    registry = default_registry()
    registered = {t.name for t in registry.list_tools()}
    declarations = {t.name: t.declaration for t in registry.list_tools()}

    from dream.permissions import SandboxTier

    for role in ("planner", "evaluator"):
        m = default_role_manifest(role)  # type: ignore[arg-type]
        # Names referenced by the manifest must exist in the registry.
        for name in (m.tools or ()):
            assert name in registered, f"{role}: unknown tool name {name!r}"
        for name in m.disallowed_tools:
            assert name in registered, f"{role}: unknown disallowed name {name!r}"
        # The effective toolset must actually grant file reading.
        effective = compute_minimum_toolset(
            m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
        )
        assert "read_file" in effective, f"{role} cannot read files: {sorted(effective)}"
        if role == "evaluator":
            assert "bash" in effective, f"evaluator cannot verify via bash: {sorted(effective)}"
