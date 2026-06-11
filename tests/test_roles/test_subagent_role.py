"""Tests for subagent RoleName + rejection from default_role_manifest / load_role_manifest.

Tests written FIRST (RED), before implementation exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.roles import default_role_manifest, load_role_manifest
from dream.roles._manifest import RoleManifest

# ---------------------------------------------------------------------------
# RoleName literal accepts "subagent"
# ---------------------------------------------------------------------------


def test_role_manifest_accepts_subagent_name() -> None:
    """RoleManifest with name='subagent' must be constructible."""
    manifest = RoleManifest(
        name="subagent",  # type: ignore[arg-type]
        description="Runtime-scoped child session.",
        system_prompt="You are a scoped subagent.",
        tools=("read_file",),
        disallowed_tools=("spawn_subagent",),
    )
    assert manifest.name == "subagent"


def test_subagent_manifest_tools_none_allowed() -> None:
    """subagent with tools=None should be valid (child can inherit all tools)."""
    manifest = RoleManifest(
        name="subagent",  # type: ignore[arg-type]
        description="Runtime-scoped child session.",
        system_prompt="You are a scoped subagent.",
        tools=None,  # None should be valid for subagent just as for generator
        disallowed_tools=("spawn_subagent",),
    )
    assert manifest.tools is None


# ---------------------------------------------------------------------------
# default_role_manifest rejects "subagent"
# ---------------------------------------------------------------------------


def test_default_role_manifest_raises_for_subagent() -> None:
    with pytest.raises(ValueError, match="subagent"):
        default_role_manifest("subagent")  # type: ignore[arg-type]


def test_default_role_manifest_error_message_says_synthesized() -> None:
    with pytest.raises(ValueError) as exc_info:
        default_role_manifest("subagent")  # type: ignore[arg-type]
    msg = str(exc_info.value).lower()
    assert "subagent" in msg


# ---------------------------------------------------------------------------
# load_role_manifest rejects "subagent"
# ---------------------------------------------------------------------------


def test_load_role_manifest_raises_for_subagent(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="subagent"):
        load_role_manifest("subagent", harness_dir=tmp_path / ".harness")  # type: ignore[arg-type]


def test_load_role_manifest_subagent_error_is_clear(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc_info:
        load_role_manifest("subagent", harness_dir=tmp_path / ".harness")  # type: ignore[arg-type]
    msg = str(exc_info.value).lower()
    assert "subagent" in msg
