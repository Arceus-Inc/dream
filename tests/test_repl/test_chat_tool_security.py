"""CodeAnt #28 / #29 -- ``/tool`` argument redaction + timeout enforcement.

These pin two security/robustness fixes on ``_cmd_tool`` in ``dream.repl._chat``:

* #28 -- ``args`` must be redacted before they are written verbatim into the
  ``tool.invoked`` JSONL event, so a secret in a ``write_file`` body or a
  ``bash`` command never lands in the plaintext audit file.
* #29 -- tool execution must honour ``declaration.timeout_seconds`` so a hung
  tool cannot block the REPL; a timeout routes into the ``tool.failed`` path.
"""

from __future__ import annotations

import asyncio
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
    _redact_args,
    _slash,
)
from dream.repl._events import EventSink
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource


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


# --- tools ---------------------------------------------------------------


class _WriteInput(BaseModel):
    path: str = ""
    content: str = ""


class _WriteTool(BaseTool):
    name = "write_file"
    description = "Pretend file writer (records the content)."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _WriteInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = _WriteInput.model_validate(input)
        return ToolResult(content=f"wrote {len(args.content)} chars to {args.path}")


class _SleepInput(BaseModel):
    pass


class _HangingTool(BaseTool):
    name = "hang"
    description = "Sleeps far longer than its declared timeout."
    # Tight timeout so the test runs fast.
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=0.1)
    input_model = _SleepInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        await asyncio.sleep(5.0)  # pragma: no cover -- cancelled by the timeout
        return ToolResult(content="never")  # pragma: no cover


def _make_state(
    tmp_path: Path, *, registry: ToolRegistry
) -> tuple[Dispatcher, Transcript, ReplState]:
    sink = EventSink(tmp_path / "events.jsonl")
    disp = Dispatcher([_ok_spec("primary")], sink)
    state = ReplState(
        stream=True,
        events_path=str(tmp_path / "events.jsonl"),
        registry=registry,
        cwd=tmp_path,
        sink=sink,
    )
    return disp, Transcript(), state


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --- #28: redaction unit -------------------------------------------------


def test_redact_args_masks_sensitive_keys() -> None:
    redacted = _redact_args({"path": "/tmp/a", "content": "TOP SECRET KEY=abc123"})
    assert redacted["path"] == "/tmp/a"
    assert "TOP SECRET" not in redacted["content"]
    assert "abc123" not in redacted["content"]
    assert "redacted" in redacted["content"]


def test_redact_args_truncates_long_nonsensitive_strings() -> None:
    long = "p" * 500
    redacted = _redact_args({"path": long})
    assert len(redacted["path"]) < len(long)


# --- #28: redaction wired into tool.invoked ------------------------------


def test_slash_tool_redacts_content_in_invoked_event(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(_WriteTool(), source=ToolSource.DEFAULT)
    disp, tr, state = _make_state(tmp_path, registry=reg)

    secret = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/superSecretValue"
    payload = json.dumps({"path": "/tmp/creds", "content": secret})
    _slash(f"/tool write_file {payload}", dispatcher=disp, transcript=tr, state=state)

    events = _read_events(tmp_path / "events.jsonl")
    invoked = next(e for e in events if e.get("type") == "tool.invoked")
    serialised = json.dumps(invoked)
    # The raw secret must not be present anywhere in the emitted event.
    assert "wJalrXUtnFEMI" not in serialised
    assert "superSecretValue" not in serialised
    # Non-sensitive args still survive for a useful audit trail.
    assert invoked["args"]["path"] == "/tmp/creds"


# --- #29: timeout enforcement --------------------------------------------


def test_slash_tool_times_out_hung_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = ToolRegistry()
    reg.register(_HangingTool(), source=ToolSource.DEFAULT)
    disp, tr, state = _make_state(tmp_path, registry=reg)
    capsys.readouterr()

    # Must return promptly (well under the tool's 5s sleep) and keep looping.
    assert _slash("/tool hang {}", dispatcher=disp, transcript=tr, state=state) is True
    out = capsys.readouterr().out.lower()
    assert "timed out" in out or "timeout" in out

    events = _read_events(tmp_path / "events.jsonl")
    failed = [e for e in events if e.get("type") == "tool.failed"]
    assert failed and failed[0]["name"] == "hang"
    assert failed[0]["error"] == "TimeoutError"
