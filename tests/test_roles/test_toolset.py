"""Spec 10 slice A — ``compute_minimum_toolset``.

The role manifest declares *intent*; the minimum toolset is the actual set
the dispatcher is allowed to honour. Spec §"Artefact shapes":

  > The *minimum* toolset is computed as `tools ∩ tier(#13)` for the
  > generator, and the fixed read-only triplet (+ contract-named verifiers)
  > for planner/evaluator — never widened from this manifest at runtime.
"""

from __future__ import annotations

from dream.permissions import SandboxTier
from dream.roles import RoleManifest, compute_minimum_toolset
from dream.tools._base import ToolDeclaration


def _decl(tier: int) -> ToolDeclaration:
    return ToolDeclaration(
        risk="safe" if tier == 0 else "mutating",
        tier_required=tier,
        timeout_seconds=10.0,
    )


# --- explicit tool list: planner/evaluator ----------------------------------


def test_planner_explicit_tools_returned_as_frozenset() -> None:
    m = RoleManifest(
        name="planner",
        description="d",
        system_prompt="p",
        tools=["file_read", "git"],
    )
    declarations = {"file_read": _decl(0), "git": _decl(0), "file_write": _decl(1)}

    out = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
    )

    assert out == frozenset({"file_read", "git"})
    assert isinstance(out, frozenset)


def test_disallowed_tools_subtract_from_explicit_list() -> None:
    m = RoleManifest(
        name="planner",
        description="d",
        system_prompt="p",
        tools=["file_read", "git", "bash"],
        disallowed_tools=["bash"],
    )
    declarations = {"file_read": _decl(0), "git": _decl(0), "bash": _decl(1)}

    out = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
    )

    assert "bash" not in out


def test_explicit_tools_filtered_by_sandbox_tier() -> None:
    # A planner that lists a write tool still gets it dropped if the
    # active sandbox tier is below the tool's required tier.
    m = RoleManifest(
        name="planner",
        description="d",
        system_prompt="p",
        tools=["file_read", "file_write"],
    )
    declarations = {"file_read": _decl(0), "file_write": _decl(1)}

    out = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.READ_ONLY, declarations=declarations
    )

    assert out == frozenset({"file_read"})


# --- null tools (generator only): intersect with tier -----------------------


def test_generator_null_tools_returns_all_registered_within_tier() -> None:
    m = RoleManifest(name="generator", description="d", system_prompt="p", tools=None)
    declarations = {
        "file_read": _decl(0),
        "file_write": _decl(1),
        "git": _decl(0),
        "external_call": _decl(2),
    }

    out = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
    )

    # tier_required <= REPO_WRITE (1): file_read(0), git(0), file_write(1).
    assert out == frozenset({"file_read", "git", "file_write"})


def test_generator_null_tools_filters_higher_tier_tools() -> None:
    m = RoleManifest(name="generator", description="d", system_prompt="p", tools=None)
    declarations = {"file_read": _decl(0), "external_call": _decl(2)}

    out = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
    )

    assert out == frozenset({"file_read"})


def test_generator_disallowed_subtracts_after_tier_intersection() -> None:
    m = RoleManifest(
        name="generator",
        description="d",
        system_prompt="p",
        tools=None,
        disallowed_tools=["bash"],
    )
    declarations = {"file_read": _decl(0), "bash": _decl(1)}

    out = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
    )

    assert out == frozenset({"file_read"})


# --- never widens from manifest ---------------------------------------------


def test_unregistered_tool_in_explicit_list_silently_dropped() -> None:
    # A manifest that names a tool absent from the registry must not surface
    # the unregistered name (capability minimisation — only honour what we
    # have a declaration for).
    m = RoleManifest(
        name="planner",
        description="d",
        system_prompt="p",
        tools=["file_read", "ghost_tool"],
    )
    declarations = {"file_read": _decl(0)}

    out = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
    )

    assert out == frozenset({"file_read"})
