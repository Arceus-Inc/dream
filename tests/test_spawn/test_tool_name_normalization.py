"""Requested-tool normalization — live-model wire-name robustness.

Live e2e exposed gpt-5.2 passing ``tools=['functions.read_file', ...]`` — the
OpenAI wire-format namespace prefix. Unnormalized, those names match nothing,
the synthesized manifest's allowlist intersects to EMPTY, and the child runs
tool-less. These tests pin the cure:

- the ``functions.`` prefix is stripped before matching;
- only KNOWN names enter the child manifest (unknowns never pollute it);
- when NOTHING requested is usable, the spawn is refused in the parent's
  turn (``SpawnUnknownToolsError`` → three-part tool error), no child runs.

All run_role calls are monkeypatched — no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dream import build_harness
from dream.runner._role_session import RunRoleResult
from dream.session import SessionCost
from dream.tools.builtin import default_registry


def _harness(tmp_path: Path) -> Any:
    (tmp_path / "wt").mkdir(parents=True, exist_ok=True)
    return build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )


def _result() -> RunRoleResult:
    return RunRoleResult(
        role="subagent",
        session_id="child-1",
        final_text="done",
        cost=SessionCost(cost_usd=0.0),
        events=(),
    )


async def _no_op_fire(payload: dict[str, Any]) -> None:
    pass


async def _spawn(
    harness: Any,
    tools: list[str] | None,
    captured: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    from dream._factory import _run_spawn

    async def _capturing_run_role(
        _harness: Any, manifest: Any, task: str, **kwargs: Any
    ) -> RunRoleResult:
        captured["manifest"] = manifest
        return _result()

    monkeypatch.setattr("dream._factory.run_role", _capturing_run_role)
    return await _run_spawn(
        harness=harness,
        tool_registry=default_registry(),
        task="t",
        tools=tools,
        child_model="m",
        child_max_turns=None,
        parent_session_id="p",
        emit=None,
        fire_subagent_stop=_no_op_fire,
    )


async def test_functions_prefix_is_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    outcome = await _spawn(
        _harness(tmp_path),
        ["functions.read_file", "functions.write_file"],
        captured,
        monkeypatch,
    )
    assert captured["manifest"].tools == ("read_file", "write_file")
    assert outcome.unknown_tools == []


async def test_unknown_names_never_enter_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    outcome = await _spawn(
        _harness(tmp_path),
        ["read_file", "definitely_not_real"],
        captured,
        monkeypatch,
    )
    assert captured["manifest"].tools == ("read_file",)
    assert outcome.unknown_tools == ["definitely_not_real"]


async def test_all_unknown_refuses_before_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dream._factory import _run_spawn
    from dream.spawn import SpawnUnknownToolsError

    called = {"run_role": False}

    async def _never(*args: Any, **kwargs: Any) -> RunRoleResult:
        called["run_role"] = True
        return _result()

    monkeypatch.setattr("dream._factory.run_role", _never)
    with pytest.raises(SpawnUnknownToolsError):
        await _run_spawn(
            harness=_harness(tmp_path),
            tool_registry=default_registry(),
            task="t",
            tools=["nope_a", "nope_b"],
            child_model="m",
            child_max_turns=None,
            parent_session_id="p",
            emit=None,
            fire_subagent_stop=_no_op_fire,
        )
    assert called["run_role"] is False  # no doomed child was spawned


async def test_tool_maps_all_unknown_to_three_part_error(tmp_path: Path) -> None:
    """The tool converts SpawnUnknownToolsError into a recoverable tool error
    (is_error=True with root_cause/safe_retry guidance), not a failed child."""
    from dream.spawn import SpawnUnknownToolsError
    from dream.spawn._context import SpawnBudget, SpawnContext
    from dream.tools._context import ToolExecutionContext
    from dream.tools.builtin.spawn_subagent import SpawnSubagentTool

    async def _raising_spawn(
        task: str, tools: list[str] | None, model: str | None, max_turns: int | None
    ) -> Any:
        raise SpawnUnknownToolsError(unknown=["nope"], available=["read_file"])

    ctx = ToolExecutionContext(
        working_dir=tmp_path,
        session_id="s",
        metadata={
            "spawn_context": SpawnContext(spawn=_raising_spawn, budget=SpawnBudget())
        },
    )
    result = await SpawnSubagentTool().execute(
        {"task": "t", "tools": ["nope"]}, ctx
    )
    assert result.is_error
    # Spec 05 contract: the three parts ride ToolResult.metadata.
    assert result.metadata["root_cause"]
    assert "read_file" in result.metadata["safe_retry"]
    assert result.metadata["stop_condition"]
