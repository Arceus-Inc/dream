"""Spec 13C.3 — make_permission_gate derives the policy from the registry.

The production gate-builder: trusted tiers come from each tool's declaration,
the policy from operator config (defaults when absent), and the result is a gate
the dispatcher can use. Proves 13A+13B+13C compose with no hand-built Policy.
"""

from __future__ import annotations

from pathlib import Path

from dream.config.paths import DreamPaths
from dream.engine._permission_gate import make_permission_gate
from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin.bash import BashTool
from dream.tools.builtin.file_read import FileReadTool
from dream.tools.builtin.file_write import FileWriteTool
from dream.utils.clock import FakeClock


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool in (FileReadTool(), FileWriteTool(), BashTool()):
        reg.register(tool, source=ToolSource.DEFAULT)
    return reg


async def test_gate_from_registry_allows_builtin_write(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path, env={})
    gate, warnings = make_permission_gate(_registry(), paths=paths, cwd=tmp_path)
    disp = EngineToolDispatcher(
        registry=_registry(), working_dir=tmp_path, session_id="s", permission_gate=gate
    )
    # write_file declares tier_required=1 (repo-write) → trusted at the default
    # repo-write tier, so an in-repo write is allowed without manual promotion.
    _c, is_error = await disp.dispatch("write_file", {"path": "ok.txt", "content": "hi"})
    assert not is_error
    assert (tmp_path / "ok.txt").read_text() == "hi"
    assert warnings == ()


async def test_gate_from_registry_denies_credential_read(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path, env={})
    gate, _ = make_permission_gate(_registry(), paths=paths, cwd=tmp_path)
    disp = EngineToolDispatcher(
        registry=_registry(), working_dir=tmp_path, session_id="s", permission_gate=gate
    )
    content, is_error = await disp.dispatch(
        "read_file", {"path": str(Path.home() / ".ssh" / "id_rsa")}
    )
    assert is_error
    assert "credential_guard" in content


async def test_gate_from_registry_denies_dangerous_command(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path, env={})
    gate, _ = make_permission_gate(_registry(), paths=paths, cwd=tmp_path)
    disp = EngineToolDispatcher(
        registry=_registry(), working_dir=tmp_path, session_id="s", permission_gate=gate
    )
    content, is_error = await disp.dispatch("bash", {"command": "rm -rf /"})
    assert is_error
    assert "command_deny" in content


def test_make_gate_surfaces_stale_promotion_warning(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "tool-tier-overrides.toml").write_text(
        '[ext]\ntier_required = "repo-write"\npromoted_at = "2000-01-01T00:00:00Z"\n',
        encoding="utf-8",
    )
    paths = DreamPaths.resolve(tmp_path, env={})
    _gate, warnings = make_permission_gate(
        _registry(), paths=paths, cwd=tmp_path, clock=FakeClock(start_ms=10**15)
    )
    assert len(warnings) == 1
