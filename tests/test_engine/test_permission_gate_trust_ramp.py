"""Spec 13C.3 — make_permission_gate honours the trust ramp for provenance.

Regression for CodeAnt PR #47: ``_trusted_tiers`` fed *every* registered tool's
declared tier into the policy, including discovered (MCP / skill / per-repo)
tools. That let a discovered tool declaring a mutating tier inherit that tier
immediately, bypassing the trust ramp documented on :class:`Policy` ("a tool
absent from the map is treated as READ_ONLY … until an operator promotes it").

A vetted built-in keeps its declared tier; a discovered tool with the *same*
declared tier must NOT — it stays untrusted (ASK) until promoted via overrides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dream.config.paths import DreamPaths
from dream.engine._permission_gate import make_permission_gate
from dream.permissions import Outcome, PermissionRequest
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin.file_write import FileWriteTool


class _MutatingInput(BaseModel):
    path: str


class _DiscoveredWriteTool(BaseTool):
    """A discovered tool that *declares* a mutating tier (e.g. an MCP adapter)."""

    name = "discovered_write"
    description = "A discovered tool claiming repo-write trust."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = _MutatingInput

    async def execute(self, input: dict[str, Any], ctx: Any) -> Any:  # pragma: no cover
        del input, ctx
        raise NotImplementedError


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    # Built-in write tool: tier_required=1, vetted → trusted at repo-write.
    reg.register(FileWriteTool(), source=ToolSource.DEFAULT)
    # Discovered tool declaring the same tier, but untrusted until promoted.
    reg.register(_DiscoveredWriteTool(), source=ToolSource.MCP)
    return reg


def _write_request(tool_name: str, cwd: Path) -> PermissionRequest:
    return PermissionRequest(
        tool_name=tool_name,
        is_read_only=False,
        target_paths=(cwd / "out.txt",),
    )


def test_builtin_write_keeps_declared_tier(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path, env={})
    gate, _ = make_permission_gate(_registry(), paths=paths, cwd=tmp_path)
    decision = gate(_write_request("write_file", tmp_path))
    assert decision.outcome is Outcome.ALLOW, decision.reason


def test_discovered_write_does_not_inherit_declared_tier(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path, env={})
    gate, _ = make_permission_gate(_registry(), paths=paths, cwd=tmp_path)
    decision = gate(_write_request("discovered_write", tmp_path))
    # Trust ramp: the discovered tool is absent from required_tier → READ_ONLY
    # trust → effectful write is ASK, not ALLOW. Promotion happens via overrides.
    assert decision.outcome is Outcome.ASK, decision.reason
    assert decision.rule == "tier_trust"


def test_operator_override_still_promotes_discovered_tool(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "tool-tier-overrides.toml").write_text(
        '[discovered_write]\ntier_required = "repo-write"\n',
        encoding="utf-8",
    )
    paths = DreamPaths.resolve(tmp_path, env={})
    gate, _ = make_permission_gate(_registry(), paths=paths, cwd=tmp_path)
    decision = gate(_write_request("discovered_write", tmp_path))
    assert decision.outcome is Outcome.ALLOW, decision.reason
