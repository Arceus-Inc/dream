"""Spec 05 per-repo ``.harness/tools/*.toml`` loader evals."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools._per_repo import PerRepoToolError, load_per_repo_tools
from dream.tools._registry import ToolSource
from dream.tools.builtin import LEVEL2_ORDER, default_registry


def _write_tool(tools_dir: Path, name: str, body: str) -> Path:
    tools_dir.mkdir(parents=True, exist_ok=True)
    path = tools_dir / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_tools_dir_is_noop(tmp_path: Path) -> None:
    reg = default_registry()
    result = load_per_repo_tools(reg, tmp_path / "missing")
    assert result.registered == ()
    assert result.warnings == ()
    assert [t.name for t in reg.list_tools()] == list(LEVEL2_ORDER)


def test_valid_toml_registers_as_per_repo(tmp_path: Path) -> None:
    tools_dir = tmp_path / ".harness" / "tools"
    _write_tool(
        tools_dir,
        "echo_hi",
        """
name = "echo_hi"
description = "Echo a message"
command = "echo {message}"
risk = "safe"
tier_required = 0
timeout_seconds = 5.0
parameters = { type = "object", properties = { message = { type = "string" } }, required = ["message"] }
""",
    )
    reg = default_registry()
    result = load_per_repo_tools(reg, tools_dir)
    assert result.registered == ("echo_hi",)
    tool = reg.get("echo_hi")
    assert tool is not None
    assert tool.declaration.risk == "safe"
    # Per-repo tools sort after defaults, alphabetically within the bucket.
    names = [t.name for t in reg.list_tools()]
    assert names[: len(LEVEL2_ORDER)] == list(LEVEL2_ORDER)
    assert names[-1] == "echo_hi"
    sources = {t.name: src for t, src in reg.iter_with_source()}
    assert sources["echo_hi"] is ToolSource.PER_REPO


def test_missing_risk_blocks_session(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    _write_tool(
        tools_dir,
        "deploy",
        """
name = "deploy"
description = "Deploy"
command = "echo deploy"
tier_required = 2
timeout_seconds = 30.0
""",
    )
    reg = default_registry()
    with pytest.raises(PerRepoToolError) as exc:
        load_per_repo_tools(reg, tools_dir)
    assert any("missing risk" in f for f in exc.value.findings)


def test_missing_tier_required_blocks_session(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    _write_tool(
        tools_dir,
        "deploy",
        """
name = "deploy"
description = "Deploy"
command = "echo deploy"
risk = "mutating"
timeout_seconds = 30.0
""",
    )
    reg = default_registry()
    with pytest.raises(PerRepoToolError) as exc:
        load_per_repo_tools(reg, tools_dir)
    assert any("tier_required" in f for f in exc.value.findings)


def test_shadow_default_warns(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    _write_tool(
        tools_dir,
        "bash",
        """
name = "bash"
description = "Repo-local bash wrapper"
command = "echo shadowed"
risk = "mutating"
tier_required = 1
timeout_seconds = 10.0
parameters = { type = "object", properties = {} }
""",
    )
    reg = default_registry()
    result = load_per_repo_tools(reg, tools_dir)
    assert "bash" in result.registered
    assert any("shadows default" in w for w in result.warnings)
    sources = {t.name: src for t, src in reg.iter_with_source()}
    assert sources["bash"] is ToolSource.PER_REPO


@pytest.mark.asyncio
async def test_per_repo_tool_runs_command_template(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    _write_tool(
        tools_dir,
        "say",
        """
name = "say"
description = "Print a word"
command = "printf '%s\\n' {word}"
risk = "safe"
tier_required = 0
timeout_seconds = 5.0
parameters = { type = "object", properties = { word = { type = "string" } }, required = ["word"] }
""",
    )
    reg = default_registry()
    load_per_repo_tools(reg, tools_dir)
    tool = reg.get("say")
    assert tool is not None
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_per_repo")
    result = await tool.execute({"word": "hello"}, ctx)
    assert result.is_error is False
    assert "hello" in result.content


def test_build_harness_loads_per_repo_tools(tmp_path: Path) -> None:
    from dream import build_harness

    wt = tmp_path / "wt"
    wt.mkdir()
    tools_dir = wt / ".harness" / "tools"
    _write_tool(
        tools_dir,
        "ping",
        """
name = "ping"
description = "Ping"
command = "echo pong"
risk = "safe"
tier_required = 0
timeout_seconds = 5.0
parameters = { type = "object", properties = {} }
""",
    )
    from dream.tools.builtin import default_registry

    registry = default_registry()
    build_harness(
        model="m",
        api_key="k",
        working_dir=wt,
        registry=registry,
        memory=False,
        env={"DREAM_HOME": str(tmp_path / "home")},
    )
    assert registry.get("ping") is not None


def test_build_harness_blocks_on_invalid_per_repo_tool(tmp_path: Path) -> None:
    from dream import build_harness

    wt = tmp_path / "wt"
    wt.mkdir()
    tools_dir = wt / ".harness" / "tools"
    _write_tool(
        tools_dir,
        "bad",
        """
name = "bad"
description = "Missing risk"
command = "echo"
tier_required = 0
timeout_seconds = 5.0
""",
    )
    with pytest.raises(ValueError, match="per-repo tool"):
        build_harness(
            model="m",
            api_key="k",
            working_dir=wt,
            memory=False,
            env={"DREAM_HOME": str(tmp_path / "home")},
        )
