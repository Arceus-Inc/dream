"""Spec 10 slice A — ``RoleManifest`` value-object invariants.

The manifest is the ``AgentDefinition``-shaped contract pinned in spec 10
§"Artefact shapes". This file pins the fields, defaults, and the validator
rules that prevent v1-forbidden shapes (``bypassPermissions``; ``tools: null``
on planner/evaluator).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dream.roles import RoleManifest


# --- fields + defaults -------------------------------------------------------


def test_manifest_minimal_fields_have_documented_defaults() -> None:
    m = RoleManifest(
        name="planner",
        description="plan a task",
        system_prompt="you are a planner",
        tools=["file_read"],
    )
    assert m.system_prompt_mode == "default"
    assert m.disallowed_tools == ()
    assert m.skills == ()
    assert m.mcp_servers == ()
    assert m.permission_mode == "default"
    assert m.isolation == "worktree"
    assert m.memory_scope == "project"
    assert m.effort == "medium"
    assert m.color == "neutral"


def test_manifest_is_frozen() -> None:
    m = RoleManifest(
        name="planner",
        description="d",
        system_prompt="p",
        tools=["file_read"],
    )
    with pytest.raises(ValidationError):
        m.name = "generator"  # type: ignore[misc]


# --- name set is fixed -------------------------------------------------------


def test_manifest_rejects_unknown_role_name() -> None:
    with pytest.raises(ValidationError):
        RoleManifest(
            name="ceo",  # type: ignore[arg-type]
            description="d",
            system_prompt="p",
            tools=["file_read"],
        )


@pytest.mark.parametrize("name", ["planner", "generator", "evaluator"])
def test_manifest_accepts_each_canonical_role_name(name: str) -> None:
    tools: list[str] | None = ["file_read"] if name != "generator" else None
    m = RoleManifest(name=name, description="d", system_prompt="p", tools=tools)  # type: ignore[arg-type]
    assert m.name == name


# --- only generator may use tools=None ---------------------------------------


@pytest.mark.parametrize("name", ["planner", "evaluator"])
def test_planner_and_evaluator_require_explicit_tool_list(name: str) -> None:
    with pytest.raises(ValidationError):
        RoleManifest(
            name=name,  # type: ignore[arg-type]
            description="d",
            system_prompt="p",
            tools=None,
        )


def test_generator_may_use_null_tools_meaning_all() -> None:
    m = RoleManifest(name="generator", description="d", system_prompt="p", tools=None)
    assert m.tools is None


# --- v1 forbidden: bypassPermissions -----------------------------------------


def test_manifest_rejects_bypass_permissions_mode() -> None:
    with pytest.raises(ValidationError):
        RoleManifest(
            name="generator",
            description="d",
            system_prompt="p",
            tools=None,
            permission_mode="bypassPermissions",  # type: ignore[arg-type]
        )


# --- system_prompt_mode ------------------------------------------------------


@pytest.mark.parametrize("mode", ["default", "replace", "append"])
def test_system_prompt_mode_accepts_documented_values(mode: str) -> None:
    m = RoleManifest(
        name="planner",
        description="d",
        system_prompt="p",
        tools=["file_read"],
        system_prompt_mode=mode,  # type: ignore[arg-type]
    )
    assert m.system_prompt_mode == mode


def test_system_prompt_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        RoleManifest(
            name="planner",
            description="d",
            system_prompt="p",
            tools=["file_read"],
            system_prompt_mode="prepend",  # type: ignore[arg-type]
        )


# --- isolation: remote requires the bridge (slice D); accepted as a value here ---


def test_manifest_accepts_remote_isolation_value() -> None:
    # The runner refuses to *spawn* remote agents without the bridge (slice D);
    # the manifest itself just carries the value.
    m = RoleManifest(
        name="generator",
        description="d",
        system_prompt="p",
        tools=None,
        isolation="remote",
    )
    assert m.isolation == "remote"
