"""Spec 05 + REPL upgrade #2 -- ``python -m dream.repl session`` subcommand.

Acceptance pinned here:
- ``session`` subparser exists with ``--events``, ``--model``, ``--system``,
  ``--max-turns`` args and a stable default events path.
- ``build_default_harness`` reads ``DREAM_SMOKE_API_KEY``/``_MODEL``/
  ``_BASE_URL`` from the supplied mapping and constructs a ``Harness``
  whose ``_engine_factory`` produces a real ``QueryEngine`` wired to
  ``OpenAIChatStreamer`` + ``default_registry()`` + a process-wide
  ``AutoCompactState``. Missing required env returns a clear error.
- ``session_loop`` consumes one prompt at a time from ``input_func``,
  routes each public ``events.Event`` through ``handle_event`` (printing
  to stdout and writing JSONL), and exits cleanly on ``/quit`` or EOF.
- ``handle_event`` writes one JSONL line per typed event with a stable
  ``session.*`` discriminator and the event's payload fields.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from dream.contracts.provider import ProviderCapabilities
from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.events import (
    Compacted,
    Error,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
    TurnComplete,
)
from dream.harness import Harness, HarnessConfig
from dream.repl.__main__ import _build_parser
from dream.repl._events import EventSink
from dream.repl._session import (
    _handle_slash,
    build_default_harness,
    handle_event,
    run_session_repl,
    session_loop,
)
from dream.repl._watch import _colour_for
from dream.services.compact._orchestrator import AutoCompactState
from dream.session import SessionOptions
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn

# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------


def test_session_subcommand_parses_default_events_path() -> None:
    parser = _build_parser()
    args = parser.parse_args(["session"])
    assert args.command == "session"
    assert args.events == Path(".dream") / "repl-events.jsonl"
    assert args.model is None
    assert args.system is None
    assert args.max_turns == 8


def test_session_subcommand_accepts_all_flags(tmp_path: Path) -> None:
    parser = _build_parser()
    events = tmp_path / "e.jsonl"
    args = parser.parse_args(
        [
            "session",
            "--events",
            str(events),
            "--model",
            "gpt-test",
            "--system",
            "be helpful",
            "--max-turns",
            "3",
        ]
    )
    assert args.events == events
    assert args.model == "gpt-test"
    assert args.system == "be helpful"
    assert args.max_turns == 3


# ---------------------------------------------------------------------------
# Default harness construction from env
# ---------------------------------------------------------------------------


def test_build_default_harness_rejects_missing_env(tmp_path: Path) -> None:
    with pytest.raises(KeyError) as ei:
        build_default_harness(env={}, working_dir=tmp_path)
    assert "DREAM_SMOKE_API_KEY" in str(ei.value) or "DREAM_SMOKE_MODEL" in str(ei.value)


def test_build_default_harness_returns_harness_with_engine_factory(
    tmp_path: Path,
) -> None:
    env = {
        "DREAM_SMOKE_API_KEY": "sk-test",
        "DREAM_SMOKE_MODEL": "gpt-test",
        "DREAM_SMOKE_BASE_URL": "http://127.0.0.1:9/v1",
    }
    harness = build_default_harness(env=env, working_dir=tmp_path)
    assert isinstance(harness, Harness)
    assert harness.config._engine_factory is not None
    engine = harness.config._engine_factory("sid", SessionOptions(model="gpt-test"))
    assert isinstance(engine, QueryEngine)
    assert engine.session_id == "sid"
    # The compactor is wired so Slice E auto-compaction is active.
    assert engine.compactor is not None


# ---------------------------------------------------------------------------
# handle_event: typed event -> stdout + JSONL
# ---------------------------------------------------------------------------


def test_handle_event_text_delta_writes_text_and_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(TextDelta(text="hello"), sink=sink, output=out)
    assert out.getvalue() == "hello"
    lines = (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[-1])
    assert payload["type"] == "session.text_delta"
    assert payload["text"] == "hello"


def test_handle_event_tool_use_start_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(
        ToolUseStart(tool_use_id="tu_1", name="bash", input={"cmd": "ls"}),
        sink=sink,
        output=out,
    )
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "session.tool_use_start"
    assert payload["tool_use_id"] == "tu_1"
    assert payload["name"] == "bash"
    assert payload["input"] == {"cmd": "ls"}


def test_handle_event_tool_use_result_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(
        ToolUseResult(tool_use_id="tu_1", name="bash", content="ok", is_error=False),
        sink=sink,
        output=out,
    )
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "session.tool_use_result"
    assert payload["is_error"] is False


def test_handle_event_turn_complete_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(
        TurnComplete(stop_reason="end_turn", usage={"in": 3, "out": 5}),
        sink=sink,
        output=out,
    )
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "session.turn_complete"
    assert payload["stop_reason"] == "end_turn"


def test_handle_event_compacted_writes_context_event(tmp_path: Path) -> None:
    """Compacted maps to ``context.compaction.completed`` so the watch
    panel's colour table (REPL upgrade #3) can pick it up alongside the
    Spec 04 context-log events.
    """
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(
        Compacted(removed_messages=3, summary_tokens=400),
        sink=sink,
        output=out,
    )
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "context.compaction.completed"
    assert payload["removed_messages"] == 3
    assert payload["summary_tokens"] == 400


def test_handle_event_error_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(Error(code="engine", message="boom"), sink=sink, output=out)
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "session.error"
    assert payload["code"] == "engine"
    assert payload["message"] == "boom"


# ---------------------------------------------------------------------------
# session_loop: drives a real Session backed by FakeStreamer
# ---------------------------------------------------------------------------


def _engine_factory(streamer: FakeStreamer) -> Any:
    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return _factory


def _scripted_input(lines: list[str]):
    """Return an ``input_func`` that yields scripted lines then EOF."""
    it = iter(lines)

    def _read(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError from exc

    return _read


async def test_session_loop_quits_on_quit_command(tmp_path: Path) -> None:
    streamer = FakeStreamer(turns=[])
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    await session_loop(
        session=session,
        sink=sink,
        input_func=_scripted_input(["/quit"]),
        output=out,
    )
    # No send fired -> no streamer turns consumed.
    assert "session " in out.getvalue()


async def test_session_loop_streams_send_events(tmp_path: Path) -> None:
    """One scripted user line, one fake assistant turn -> the loop yields
    TextDelta + TurnComplete to stdout and JSONL, then EOF cleanly exits.
    """
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["hi ", "there"],
                usage=UsageSnapshot(input_tokens=2, output_tokens=2),
            )
        ]
    )
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    await session_loop(
        session=session,
        sink=sink,
        input_func=_scripted_input(["hello"]),
        output=out,
    )
    text = out.getvalue()
    assert "hi " in text
    assert "there" in text
    lines = [
        json.loads(line) for line in (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    types = [r["type"] for r in lines]
    assert "session.text_delta" in types
    assert "session.turn_complete" in types


async def test_session_loop_info_command_prints_session_id(tmp_path: Path) -> None:
    streamer = FakeStreamer(turns=[])
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    await session_loop(
        session=session,
        sink=sink,
        input_func=_scripted_input(["/info", "/quit"]),
        output=out,
    )
    assert session.id in out.getvalue()


async def test_session_loop_unknown_slash_keeps_running(tmp_path: Path) -> None:
    streamer = FakeStreamer(turns=[])
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    await session_loop(
        session=session,
        sink=sink,
        input_func=_scripted_input(["/nope", "/quit"]),
        output=out,
    )
    assert "unknown" in out.getvalue().lower()


# ---------------------------------------------------------------------------
# run_session_repl: top-level entry — accepts injected harness
# ---------------------------------------------------------------------------


def test_run_session_repl_with_injected_harness_returns_zero(
    tmp_path: Path,
) -> None:
    streamer = FakeStreamer(turns=[])
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    out = io.StringIO()
    rc = run_session_repl(
        events_path=tmp_path / "e.jsonl",
        harness=harness,
        input_func=_scripted_input(["/quit"]),
        output=out,
    )
    assert rc == 0
    lines = [
        json.loads(line) for line in (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    types = [r["type"] for r in lines]
    assert "session.repl.started" in types
    assert "session.repl.stopped" in types


def test_run_session_repl_without_env_or_harness_returns_nonzero(
    tmp_path: Path,
) -> None:
    out = io.StringIO()
    rc = run_session_repl(
        events_path=tmp_path / "e.jsonl",
        env={},
        input_func=_scripted_input([]),
        output=out,
    )
    assert rc != 0


# ---------------------------------------------------------------------------
# REPL upgrade #3 -- /util and /compact slash commands
# ---------------------------------------------------------------------------


def _engine_factory_with_compactor(
    streamer: FakeStreamer,
    *,
    compactor: AutoCompactState,
    capabilities: ProviderCapabilities,
):
    """Like ``_engine_factory`` but wires the Spec 04 compaction inputs.

    The two extra fields are what ``/util`` and ``/compact`` read off the
    Session's bound engine. ``compactor`` must be non-``None`` for
    ``/compact`` to do anything; the capabilities supply the denominator
    for ``utilisation``.
    """

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
            compactor=compactor,
            compaction_capabilities=capabilities,
        )

    return _factory


async def _session_with_compactor() -> tuple[Any, AutoCompactState, ProviderCapabilities]:
    compactor = AutoCompactState()
    capabilities = ProviderCapabilities(max_context_tokens=128_000)
    streamer = FakeStreamer(turns=[])
    harness = Harness(
        HarnessConfig(  # type: ignore[call-arg]
            _engine_factory=_engine_factory_with_compactor(
                streamer, compactor=compactor, capabilities=capabilities
            )
        )
    )
    session = await harness.start_session()
    return session, compactor, capabilities


async def test_handle_slash_util_prints_utilisation_and_cost(tmp_path: Path) -> None:
    """``/util`` reports utilisation% against the engine's capabilities and
    the Session's running cost. Emits a ``session.util`` mirror event so
    the watch panel can record it.
    """
    session, _compactor, _caps = await _session_with_compactor()
    session._transcript.append(ConversationMessage(role="user", content=[TextBlock(text="hi")]))
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    keep = _handle_slash("/util", session=session, sink=sink, output=out)
    assert keep is True
    text = out.getvalue().lower()
    assert "util" in text
    assert "%" in text
    assert "cost" in text or "in=" in text
    lines = (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[-1])
    assert payload["type"] == "session.util"


async def test_handle_slash_compact_runs_microcompact_and_emits_event(
    tmp_path: Path,
) -> None:
    """``/compact`` forces a manual compaction via ``auto_compact_if_needed``
    (``trigger="manual"``, ``force=True``) so older compactable tool
    results are replaced with the cleared-content sentinel. The mirror
    event lands as ``context.compaction.completed`` so REPL watch
    colours it consistently with Spec 04 events.
    """
    session, _compactor, _caps = await _session_with_compactor()
    # Seed the transcript with >5 compactable tool_use/result pairs so
    # microcompact has something to clear (DEFAULT_KEEP_RECENT == 5).
    big_blob = "X" * 4096
    for i in range(8):
        tu_id = f"tu_{i}"
        session._transcript.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=tu_id, name="bash", input={"cmd": "ls"})],
            )
        )
        session._transcript.append(
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id=tu_id, content=big_blob, is_error=False)],
            )
        )
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()

    pre_blob_count = sum(
        1
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, ToolResultBlock) and block.content == big_blob
    )
    assert pre_blob_count == 8

    keep = _handle_slash("/compact", session=session, sink=sink, output=out)
    assert keep is True

    post_blob_count = sum(
        1
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, ToolResultBlock) and block.content == big_blob
    )
    # At least some older tool results should now carry the cleared marker.
    assert post_blob_count < pre_blob_count

    lines = (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines]
    types = [p["type"] for p in payloads]
    assert "context.compaction.completed" in types
    assert any("compact" in line.lower() for line in out.getvalue().splitlines())


async def test_handle_slash_compact_no_compactor_warns(tmp_path: Path) -> None:
    """If the engine has no compactor wired, ``/compact`` is a no-op that
    prints a helpful message instead of crashing.
    """
    streamer = FakeStreamer(turns=[])

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    harness = Harness(HarnessConfig(_engine_factory=_factory))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    keep = _handle_slash("/compact", session=session, sink=sink, output=out)
    assert keep is True
    text = out.getvalue().lower()
    assert "compact" in text
    assert "not" in text or "disabled" in text or "no compactor" in text


# ---------------------------------------------------------------------------
# REPL upgrade #3 -- watch colour table
# ---------------------------------------------------------------------------


def test_watch_colour_for_context_compaction_completed_is_cyan() -> None:
    """``context.compaction.completed`` is the public-facing 'compaction
    succeeded' event the REPL watch should highlight in cyan.
    """
    code = _colour_for("context.compaction.completed")
    assert code != ""
    assert "36" in code  # ANSI cyan


def test_watch_colour_for_context_compaction_triggered_is_yellow() -> None:
    """``context.compaction.triggered`` signals pressure; render in yellow."""
    code = _colour_for("context.compaction.triggered")
    assert code != ""
    assert "33" in code  # ANSI yellow


def test_watch_colour_for_session_turn_complete_is_green() -> None:
    code = _colour_for("session.turn_complete")
    assert "32" in code  # ANSI green


def test_watch_colour_for_session_error_is_red() -> None:
    code = _colour_for("session.error")
    assert "31" in code  # ANSI red


def test_watch_colour_for_session_repl_started_is_cyan() -> None:
    code = _colour_for("session.repl.started")
    assert "36" in code  # ANSI cyan
