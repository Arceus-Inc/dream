"""Spec 05 slice C — ``EngineToolDispatcher`` bridges ``ToolRegistry``
to the engine's narrow ``ToolDispatcher`` Protocol (``_loop.py``).

Pipeline pinned by these tests:

1. ``registry.get(name)`` is ``None`` → typed ``is_error`` result, no execute.
2. ``tool.input_model.model_validate(input)`` raises → typed ``is_error``
   result with the 3-part-contract hint, no execute.
3. Per-call ``is_read_only`` is computed before execute and surfaced to the
   injected observer.
4. ``tool.execute`` runs under ``declaration.timeout_seconds`` via
   ``asyncio.wait_for``; timeout becomes a typed ``is_error`` result with
   the 3-part-contract hint.
5. Oversized ``ToolResult.content`` is routed through
   ``services.tool_outputs.offload_tool_output`` so the returned content is
   an inline preview + ref token, with the artifact spilled to scratch.
6. Exceptions raised by ``tool.execute`` are **not** caught here — they
   propagate so the engine loop's existing non-revealing failure marker
   applies unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.engine._tool_dispatch import DispatchRecord, EngineToolDispatcher
from dream.services.tool_outputs import DEFAULT_TOOL_OUTPUT_INLINE_CHARS
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource

# --- local fakes -------------------------------------------------------------


class _EchoInput(BaseModel):
    text: str = Field(..., min_length=1)


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echo a string back."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _EchoInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=f"echo:{input['text']}", metadata={"echoed": True})


class _BigInput(BaseModel):
    size: int = Field(default=DEFAULT_TOOL_OUTPUT_INLINE_CHARS * 2, ge=1)


class _BigTool(BaseTool):
    """Returns a ``size``-character body, used to trigger the offload path."""

    name = "bigtool"
    description = "Emit a payload of arbitrary size."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _BigInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        n = int(input.get("size", DEFAULT_TOOL_OUTPUT_INLINE_CHARS * 2))
        return ToolResult(content="x" * n, metadata={"summary": f"{n} chars"})


class _SlowInput(BaseModel):
    seconds: float = Field(default=1.0, gt=0)


class _SlowTool(BaseTool):
    """Sleeps longer than its declared timeout so ``wait_for`` fires."""

    name = "slow"
    description = "Sleep for a while."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=0.05)
    input_model = _SlowInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        await asyncio.sleep(float(input.get("seconds", 1.0)))
        return ToolResult(content="done")


class _BoomInput(BaseModel):
    pass


class _BoomTool(BaseTool):
    """Always raises so we can prove the dispatcher does NOT swallow it."""

    name = "boom"
    description = "Raise an exception."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _BoomInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        raise RuntimeError("kaboom -- engine internals, do not leak to model")


class _CtxInspectInput(BaseModel):
    pass


class _CtxInspectTool(BaseTool):
    """Records the ``ToolExecutionContext`` it receives so tests can assert."""

    name = "ctx_inspect"
    description = "Echo the working_dir / session_id back via metadata."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _CtxInspectInput

    last_ctx: ToolExecutionContext | None = None

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        type(self).last_ctx = ctx
        return ToolResult(content="ok")


class _CondReadOnlyInput(BaseModel):
    mode: str = "read"


class _CondReadOnlyTool(BaseTool):
    """``is_read_only_for`` downclassifies a specific invocation."""

    name = "cond"
    description = "Mutating overall but read-only for ``mode='read'``."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=5.0)
    input_model = _CondReadOnlyInput

    def is_read_only_for(self, input: dict[str, Any]) -> bool:
        return input.get("mode") == "read"

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=f"cond:{input.get('mode')}")


# --- helpers -----------------------------------------------------------------


def _registry(*tools: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t, source=ToolSource.DEFAULT)
    return reg


def _records() -> tuple[list[DispatchRecord], Any]:
    """Return (sink_list, callable). The callable appends each record."""
    sink: list[DispatchRecord] = []

    def _record(rec: DispatchRecord) -> None:
        sink.append(rec)

    return sink, _record


# --- 1. unknown tool ---------------------------------------------------------


async def test_unknown_tool_returns_typed_error_and_does_not_execute(tmp_path: Path) -> None:
    reg = _registry(_EchoTool())
    sink, recorder = _records()
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        on_dispatch=recorder,
    )

    content, is_error = await disp.dispatch("nope", {})

    assert is_error is True
    assert "nope" in content.lower() or "unknown" in content.lower()
    assert "root_cause" in content
    # Recorder still fires so observability never misses a call.
    assert [r.tool_name for r in sink] == ["nope"]
    assert sink[0].is_error is True


async def test_unknown_tool_lists_known_names_for_recovery(tmp_path: Path) -> None:
    reg = _registry(_EchoTool(), _CondReadOnlyTool())
    disp = EngineToolDispatcher(registry=reg, working_dir=tmp_path, session_id="s")

    content, _ = await disp.dispatch("nope", {})

    # The model should be able to see what names are valid.
    assert "echo" in content
    assert "cond" in content


# --- 2. schema-invalid input -------------------------------------------------


async def test_schema_invalid_input_returns_typed_error_no_execute(tmp_path: Path) -> None:
    reg = _registry(_EchoTool())
    _sink, recorder = _records()
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        on_dispatch=recorder,
    )

    # ``text`` has ``min_length=1`` so empty string fails validation.
    content, is_error = await disp.dispatch("echo", {"text": ""})

    assert is_error is True
    assert "echo" in content
    assert "root_cause" in content
    # No model-derived ``echoed`` metadata can have been produced, so the
    # content cannot start with the success prefix the tool would emit.
    assert not content.startswith("echo:")


async def test_schema_invalid_missing_required_field(tmp_path: Path) -> None:
    reg = _registry(_EchoTool())
    disp = EngineToolDispatcher(registry=reg, working_dir=tmp_path, session_id="s")

    content, is_error = await disp.dispatch("echo", {})  # missing ``text``

    assert is_error is True
    assert "echo" in content
    assert "root_cause" in content


# --- 3. per-call is_read_only ------------------------------------------------


async def test_dispatch_records_per_call_read_only_true(tmp_path: Path) -> None:
    reg = _registry(_CondReadOnlyTool())
    sink, recorder = _records()
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        on_dispatch=recorder,
    )

    await disp.dispatch("cond", {"mode": "read"})

    assert len(sink) == 1
    assert sink[0].tool_name == "cond"
    assert sink[0].is_read_only is True
    assert sink[0].is_error is False


async def test_dispatch_records_per_call_read_only_false(tmp_path: Path) -> None:
    reg = _registry(_CondReadOnlyTool())
    sink, recorder = _records()
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        on_dispatch=recorder,
    )

    await disp.dispatch("cond", {"mode": "write"})

    assert len(sink) == 1
    assert sink[0].is_read_only is False


# --- 4. timeout --------------------------------------------------------------


async def test_dispatch_timeout_returns_typed_error_with_three_part_contract(
    tmp_path: Path,
) -> None:
    reg = _registry(_SlowTool())
    sink, recorder = _records()
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        on_dispatch=recorder,
    )

    content, is_error = await disp.dispatch("slow", {"seconds": 1.0})

    assert is_error is True
    assert "timeout" in content.lower() or "timed out" in content.lower()
    assert "root_cause" in content
    assert "safe_retry" in content
    assert "stop_condition" in content
    # Observer sees the failed dispatch and the read-only flag of the tool.
    assert sink[0].is_error is True


async def test_dispatch_within_timeout_returns_success(tmp_path: Path) -> None:
    reg = _registry(_EchoTool())
    disp = EngineToolDispatcher(registry=reg, working_dir=tmp_path, session_id="s")

    content, is_error = await disp.dispatch("echo", {"text": "hi"})

    assert is_error is False
    assert content == "echo:hi"


# --- 5. offload oversized output --------------------------------------------


async def test_dispatch_offloads_oversized_output(tmp_path: Path) -> None:
    """Result.content larger than the inline budget is spilled to scratch."""
    scratch = tmp_path / "scratch"
    reg = _registry(_BigTool())
    sink, recorder = _records()
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        scratch_dir=scratch,
        on_dispatch=recorder,
    )

    payload_size = DEFAULT_TOOL_OUTPUT_INLINE_CHARS * 4
    content, is_error = await disp.dispatch("bigtool", {"size": payload_size})

    assert is_error is False
    # Inline content is the truncation banner, not the raw payload.
    assert len(content) < payload_size
    assert "truncated" in content.lower()
    # A sidecar artifact exists in scratch.
    assert scratch.exists()
    spilled = list(scratch.iterdir())
    assert len(spilled) == 1, spilled
    assert spilled[0].read_text(encoding="utf-8") == "x" * payload_size
    # Observer notes the offload.
    assert sink[0].offloaded is True


async def test_dispatch_does_not_offload_small_output(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    reg = _registry(_EchoTool())
    sink, recorder = _records()
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        scratch_dir=scratch,
        on_dispatch=recorder,
    )

    content, is_error = await disp.dispatch("echo", {"text": "small"})

    assert is_error is False
    assert content == "echo:small"
    # Scratch is created lazily by the offload only when needed; for a small
    # payload it must not be touched.
    assert not scratch.exists()
    assert sink[0].offloaded is False


# --- 6. exception passthrough -----------------------------------------------


async def test_dispatch_propagates_tool_exception_unchanged(tmp_path: Path) -> None:
    """The dispatcher does NOT swallow ``tool.execute`` exceptions.

    The engine loop's ``run_query`` already turns raised exceptions into a
    generic non-revealing transcript marker (see ``_loop.py``). Catching
    here would defeat that and risk leaking internals into the transcript.
    """
    reg = _registry(_BoomTool())
    sink, recorder = _records()
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        on_dispatch=recorder,
    )

    with pytest.raises(RuntimeError, match="kaboom"):
        await disp.dispatch("boom", {})

    # Recorder is NOT called when execute raises -- the loop owns that record.
    assert sink == []


# --- 7. context wiring -------------------------------------------------------


async def test_dispatch_passes_working_dir_and_session_id_to_tool_ctx(
    tmp_path: Path,
) -> None:
    reg = _registry(_CtxInspectTool())
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s_engine",
    )

    await disp.dispatch("ctx_inspect", {})

    ctx = _CtxInspectTool.last_ctx
    assert ctx is not None
    assert ctx.working_dir == tmp_path
    assert ctx.session_id == "s_engine"


async def test_dispatch_passes_scratch_dir_to_tool_ctx(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    reg = _registry(_CtxInspectTool())
    disp = EngineToolDispatcher(
        registry=reg,
        working_dir=tmp_path,
        session_id="s",
        scratch_dir=scratch,
    )

    await disp.dispatch("ctx_inspect", {})

    ctx = _CtxInspectTool.last_ctx
    assert ctx is not None
    assert ctx.scratch_dir == scratch


# --- 8. observer optional ----------------------------------------------------


async def test_dispatch_works_without_observer(tmp_path: Path) -> None:
    reg = _registry(_EchoTool())
    disp = EngineToolDispatcher(registry=reg, working_dir=tmp_path, session_id="s")

    content, is_error = await disp.dispatch("echo", {"text": "noop"})

    assert is_error is False
    assert content == "echo:noop"


# --- 9. satisfies engine ToolDispatcher Protocol ----------------------------


def test_engine_tool_dispatcher_satisfies_loop_protocol(tmp_path: Path) -> None:
    """Structural check: assign to a ``ToolDispatcher``-typed slot."""
    from dream.engine._loop import ToolDispatcher

    reg = _registry(_EchoTool())
    disp: ToolDispatcher = EngineToolDispatcher(registry=reg, working_dir=tmp_path, session_id="s")

    # If the structural type fails, the assignment line would be flagged by
    # mypy -- a runtime sanity assertion still adds value as a guard against
    # someone accidentally renaming the ``dispatch`` method.
    assert hasattr(disp, "dispatch")
    assert callable(disp.dispatch)
