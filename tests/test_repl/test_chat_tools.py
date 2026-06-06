"""Unit pins for ``/tools`` + ``/tool`` REPL slash commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from dream.api.credentials import Credential
from dream.api.substrate import CompletionResult, HealthReport
from dream.contracts.tool import ToolResult
from dream.repl._chat import (
    Dispatcher,
    ReplState,
    SubstrateSpec,
    Transcript,
    _slash,
)
from dream.repl._events import EventSink
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource

# --- minimal ok substrate (no LLM calls under test) ---------------------


class _OkSub:
    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(self, **kwargs: Any) -> CompletionResult:  # pragma: no cover
        return CompletionResult(text="ok", input_tokens=0, output_tokens=0)

    async def stream(self, **kwargs: Any):  # pragma: no cover
        yield "ok"

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def max_window(self) -> int:
        return 8_192

    def health(self) -> HealthReport:
        return HealthReport(state="ok", detail="", latency_ms=1.0)


def _ok_spec(name: str) -> SubstrateSpec:
    return SubstrateSpec(
        name=name,
        model="ok",
        base_url=None,
        max_window=8_192,
        timeout_seconds=5.0,
        credentials=[Credential(label="only", key="ok", substrate=name)],
        builder=lambda _cred: _OkSub(name),
    )


# --- fake tools ----------------------------------------------------------


class _EchoInput(BaseModel):
    x: int = 0


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echoes x as content."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _EchoInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = _EchoInput.model_validate(input)
        return ToolResult(
            content=f"x={args.x}",
            metadata={"summary": f"echoed x={args.x}"},
        )


class _FailingInput(BaseModel):
    pass


class _FailingTool(BaseTool):
    name = "fail"
    description = "Always returns is_error=True."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _FailingInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(
            content="boom",
            is_error=True,
            metadata={
                "root_cause": "intentional test failure",
                "safe_retry": "do not retry",
                "stop_condition": "test asserts is_error path",
            },
        )


# --- fixtures ------------------------------------------------------------


def _make_state(
    tmp_path: Path, *, registry: ToolRegistry | None = None
) -> tuple[Dispatcher, Transcript, ReplState, EventSink]:
    sink = EventSink(tmp_path / "events.jsonl")
    disp = Dispatcher([_ok_spec("primary")], sink)
    state = ReplState(
        stream=True,
        events_path=str(tmp_path / "events.jsonl"),
        registry=registry,
        cwd=tmp_path,
        sink=sink,
    )
    return disp, Transcript(), state, sink


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --- /tools --------------------------------------------------------------


def test_slash_tools_with_empty_registry_prints_placeholder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = ToolRegistry()
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()  # flush bootstrap noise
    assert _slash("/tools", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "no tools registered" in out


def test_slash_tools_lists_registered_tools_in_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = ToolRegistry()
    reg.register(_EchoTool(), source=ToolSource.DEFAULT)
    reg.register(_FailingTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()
    _slash("/tools", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "echo" in out
    assert "fail" in out
    # Risk + tier + timeout surface.
    assert "safe" in out
    assert "tier=0" in out
    # Order: echo before fail (alphabetical leftover defaults).
    assert out.index("echo") < out.index("fail")


def test_slash_tools_without_registry_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disp, tr, state, _ = _make_state(tmp_path, registry=None)
    capsys.readouterr()
    assert _slash("/tools", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "registry not configured" in out


# --- /tool ---------------------------------------------------------------


def test_slash_tool_no_arg_prints_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg = ToolRegistry()
    reg.register(_EchoTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()
    assert _slash("/tool", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_slash_tool_unknown_name_reports_and_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = ToolRegistry()
    reg.register(_EchoTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()
    assert _slash("/tool missing {}", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "unknown tool" in out.lower()
    # No invocation events should land for the missing name.
    events = _read_events(tmp_path / "events.jsonl")
    assert not any(e.get("type", "").startswith("tool.") for e in events)


def test_slash_tool_invalid_json_reports_parse_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = ToolRegistry()
    reg.register(_EchoTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()
    assert _slash("/tool echo {not-json", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "json" in out.lower()


def test_slash_tool_defaults_to_empty_json_when_args_omitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = ToolRegistry()
    reg.register(_EchoTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()
    assert _slash("/tool echo", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out
    assert "x=0" in out  # default x=0 from pydantic


def test_slash_tool_runs_tool_and_prints_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = ToolRegistry()
    reg.register(_EchoTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()
    _slash('/tool echo {"x": 7}', dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "x=7" in out


def test_slash_tool_emits_invoked_and_completed_events(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(_EchoTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    _slash('/tool echo {"x": 3}', dispatcher=disp, transcript=tr, state=state)
    events = _read_events(tmp_path / "events.jsonl")
    tool_events = [e for e in events if str(e.get("type", "")).startswith("tool.")]
    types = [e["type"] for e in tool_events]
    assert "tool.invoked" in types
    assert "tool.completed" in types
    completed = next(e for e in tool_events if e["type"] == "tool.completed")
    assert completed["name"] == "echo"
    assert completed["is_error"] is False


def test_slash_tool_emits_failed_event_when_tool_returns_is_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = ToolRegistry()
    reg.register(_FailingTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()
    _slash("/tool fail {}", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "error" in out.lower()
    # Error-recovery metadata surfaces to the user.
    assert "root_cause" in out
    events = _read_events(tmp_path / "events.jsonl")
    failed = [e for e in events if e.get("type") == "tool.failed"]
    assert failed and failed[0]["name"] == "fail"


def test_slash_tool_validation_error_caught_and_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wrong-shaped JSON (valid syntax, fails pydantic validation) is user-facing."""
    reg = ToolRegistry()
    reg.register(_EchoTool(), source=ToolSource.DEFAULT)
    disp, tr, state, _ = _make_state(tmp_path, registry=reg)
    capsys.readouterr()
    # x must be int; string triggers pydantic ValidationError inside execute().
    assert (
        _slash(
            '/tool echo {"x": "not-an-int"}',
            dispatcher=disp,
            transcript=tr,
            state=state,
        )
        is True
    )
    out = capsys.readouterr().out
    assert "error" in out.lower()


def test_help_includes_tool_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    disp, tr, state, _ = _make_state(tmp_path, registry=ToolRegistry())
    capsys.readouterr()
    _slash("/help", dispatcher=disp, transcript=tr, state=state)
    out = capsys.readouterr().out
    assert "/tools" in out
    assert "/tool" in out
