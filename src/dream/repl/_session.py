"""``python -m dream.repl session`` -- interactive Spec 05 Session loop.

Wires the Spec 02 OpenAI-compatible adapter into a Spec 05 Harness so a
single REPL talks to a real provider through ``Session.send``, streaming
typed ``events.Event`` values to stdout while mirroring them as JSONL to
the same sink the ``watch`` subcommand tails.

Slash commands: ``/help``, ``/quit`` / ``/exit``, ``/info``, ``/reset``,
``/util`` (current context utilisation% + cost), and ``/compact`` (force
a manual Spec 04 compaction on the bound engine's transcript).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from dream.contracts.provider import ProviderCapabilities
from dream.engine._adapter_openai import (
    OpenAIChatStreamer,
    httpx_chat_completion_stream,
)
from dream.engine._engine import QueryEngine, build_query_engine
from dream.events import (
    Compacted,
    Error,
    Event,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
    TurnComplete,
)
from dream.harness import Harness, HarnessConfig
from dream.repl._events import EventSink
from dream.services.compact._orchestrator import (
    AutoCompactState,
    auto_compact_if_needed,
)
from dream.services.token_estimation import utilisation
from dream.session import Session, SessionOptions
from dream.tools.builtin import default_registry

# ---------------------------------------------------------------------------
# Default harness construction from env
# ---------------------------------------------------------------------------


_REQUIRED_ENV = ("DREAM_SMOKE_API_KEY", "DREAM_SMOKE_MODEL")


def _missing(env: Mapping[str, str]) -> list[str]:
    return [k for k in _REQUIRED_ENV if not env.get(k)]


def build_default_harness(
    *,
    env: Mapping[str, str],
    working_dir: Path,
    max_turns: int = 8,
) -> Harness:
    """Build a Harness whose ``_engine_factory`` produces a real engine.

    Reads ``DREAM_SMOKE_API_KEY``, ``DREAM_SMOKE_MODEL`` and
    ``DREAM_SMOKE_BASE_URL`` from ``env``. Each ``start_session`` call
    constructs a fresh ``OpenAIChatStreamer`` so per-session
    ``system_prompt`` / ``model`` overrides take effect; the
    ``ToolRegistry`` and ``AutoCompactState`` are shared so registrations
    and compaction-cooldown state survive across sessions in the same
    REPL process.
    """
    missing = _missing(env)
    if missing:
        raise KeyError("missing required env vars: " + ", ".join(missing))
    api_key = env["DREAM_SMOKE_API_KEY"]
    model = env["DREAM_SMOKE_MODEL"]
    base_url = env.get("DREAM_SMOKE_BASE_URL", "https://api.openai.com/v1")
    registry = default_registry()
    compactor = AutoCompactState()
    # 128K is the default we use throughout Spec 02; the watch panel /
    # /util command surface utilisation against this number.
    capabilities = ProviderCapabilities(max_context_tokens=128_000)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        streamer = OpenAIChatStreamer(
            stream_chat_completion=httpx_chat_completion_stream(
                api_key=api_key,
                base_url=base_url,
            ),
            model=options.model or model,
            system_prompt=options.system_prompt,
        )
        return build_query_engine(
            streamer=streamer,
            registry=registry,
            session_id=session_id,
            working_dir=working_dir,
            max_turns=options.max_turns or max_turns,
            compactor=compactor,
            compaction_capabilities=capabilities,
        )

    return Harness(HarnessConfig(working_dir=working_dir, _engine_factory=_factory))


# ---------------------------------------------------------------------------
# Per-event rendering
# ---------------------------------------------------------------------------


def handle_event(ev: Event, *, sink: EventSink, output: TextIO) -> None:
    """Render one public ``Event`` to ``output`` + JSONL sink.

    The JSONL discriminator namespace is ``session.*`` for the engine's
    own events and ``context.compaction.completed`` for ``Compacted`` so
    the REPL #3 watch-panel colour table can route it alongside the
    Spec 04 context-log events.
    """
    if isinstance(ev, TextDelta):
        output.write(ev.text)
        output.flush()
        sink.emit("session.text_delta", text=ev.text)
    elif isinstance(ev, ToolUseStart):
        output.write(f"\n[tool {ev.name}({ev.tool_use_id})] {ev.input}\n")
        sink.emit(
            "session.tool_use_start",
            tool_use_id=ev.tool_use_id,
            name=ev.name,
            input=ev.input,
        )
    elif isinstance(ev, ToolUseResult):
        marker = " (error)" if ev.is_error else ""
        snippet = ev.content if len(ev.content) <= 200 else ev.content[:200] + "..."
        output.write(f"[tool {ev.name} result{marker}] {snippet}\n")
        sink.emit(
            "session.tool_use_result",
            tool_use_id=ev.tool_use_id,
            name=ev.name,
            is_error=ev.is_error,
            content_chars=len(ev.content),
        )
    elif isinstance(ev, TurnComplete):
        output.write(f"\n[turn done stop={ev.stop_reason}]\n")
        sink.emit(
            "session.turn_complete",
            stop_reason=ev.stop_reason,
            usage=dict(ev.usage),
        )
    elif isinstance(ev, Compacted):
        output.write(
            f"[compacted removed={ev.removed_messages} summary_tokens={ev.summary_tokens}]\n"
        )
        sink.emit(
            "context.compaction.completed",
            removed_messages=ev.removed_messages,
            summary_tokens=ev.summary_tokens,
        )
    elif isinstance(ev, Error):
        output.write(f"[error {ev.code}] {ev.message}\n")
        sink.emit("session.error", code=ev.code, message=ev.message)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def _handle_slash(line: str, *, session: Session, sink: EventSink, output: TextIO) -> bool:
    """Dispatch one slash command. Returns True to keep looping, False to quit."""
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    if cmd in ("/quit", "/exit"):
        return False
    if cmd in ("/help", "/?"):
        output.write(
            "commands: /help /quit /info /reset /util /compact\n"
            "anything else is sent to the model.\n"
        )
        return True
    if cmd == "/info":
        output.write(
            f"session id={session.id}\n"
            f"model={session.options.model or '<default>'}\n"
            f"cost in={session.cost.input_tokens} "
            f"out={session.cost.output_tokens}\n"
        )
        sink.emit("session.info", session_id=session.id)
        return True
    if cmd == "/util":
        # Read capabilities off the bound engine; if absent we still
        # render a usable line (utilisation falls back to 0.0).
        engine = session._engine
        capabilities = getattr(engine, "compaction_capabilities", None) if engine else None
        pct = utilisation(session._transcript, capabilities) * 100.0
        cost = session.cost
        output.write(
            f"util {pct:.1f}% messages={len(session._transcript)} "
            f"cost in={cost.input_tokens} out={cost.output_tokens}\n"
        )
        sink.emit(
            "session.util",
            session_id=session.id,
            utilisation=pct / 100.0,
            messages=len(session._transcript),
            input_tokens=cost.input_tokens,
            output_tokens=cost.output_tokens,
        )
        return True
    if cmd == "/compact":
        engine = session._engine
        compactor = getattr(engine, "compactor", None) if engine else None
        if engine is None or compactor is None:
            output.write("[/compact] no compactor wired on this engine\n")
            sink.emit("session.compact_skipped", reason="no_compactor")
            return True
        capabilities = engine.compaction_capabilities
        threshold = engine.compaction_threshold
        preserve_recent = engine.compaction_preserve_recent
        pre_count = len(session._transcript)
        new_transcript, result = auto_compact_if_needed(
            session._transcript,
            capabilities=capabilities,
            state=compactor,
            trigger="manual",
            threshold=threshold,
            preserve_recent=preserve_recent,
            force=True,
        )
        session._transcript[:] = new_transcript
        removed = max(0, pre_count - len(new_transcript))
        if result is None:
            output.write("[/compact] nothing to compact\n")
            sink.emit("session.compact_skipped", reason="noop")
            return True
        post_util = utilisation(new_transcript, capabilities)
        output.write(
            f"[/compact] tier={result.tier} removed_messages={removed} "
            f"util_after={post_util * 100.0:.1f}%\n"
        )
        sink.emit(
            "context.compaction.completed",
            tier=result.tier,
            removed_messages=removed,
            resulting_utilisation=post_util,
        )
        return True
    if cmd == "/reset":
        # Spec 05 Session has no public reset hook yet, so we just drop
        # the in-memory transcript via the private attribute. The next
        # send starts from a clean slate while keeping the engine and
        # cost counters intact.
        session._transcript.clear()
        output.write("[transcript cleared]\n")
        sink.emit("session.reset", session_id=session.id)
        return True
    output.write(f"unknown command {cmd!r}; /help for list\n")
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def session_loop(
    *,
    session: Session,
    sink: EventSink,
    input_func: Callable[[str], str] = input,
    output: TextIO | None = None,
) -> None:
    """Drive a Session against an input/output pair.

    ``input_func`` is called once per turn; raise ``EOFError`` /
    ``KeyboardInterrupt`` to exit cleanly. ``output`` defaults to
    ``sys.stdout``. Streaming reads run on the asyncio event loop;
    the synchronous ``input_func`` is awaited via ``asyncio.to_thread``
    so a long-running send doesn't block stdin.
    """
    out = output if output is not None else sys.stdout
    out.write(f"session {session.id[:8]} model={session.options.model or '<default>'}\n")
    out.write("type /help for commands, /quit to exit\n")
    out.flush()

    while True:
        try:
            line = await asyncio.to_thread(input_func, "> ")
        except (EOFError, KeyboardInterrupt):
            out.write("\n")
            break
        if not line.strip():
            continue
        if line.startswith("/"):
            if not _handle_slash(line, session=session, sink=sink, output=out):
                break
            continue
        try:
            async for ev in session.send(line):
                handle_event(ev, sink=sink, output=out)
        except Exception as exc:
            out.write(f"[turn failed] {type(exc).__name__}: {exc}\n")
            sink.emit(
                "session.turn_failed",
                exc_type=type(exc).__name__,
                message=str(exc),
            )
        out.write("\n")
        out.flush()


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def run_session_repl(
    *,
    events_path: Path,
    model: str | None = None,
    system: str | None = None,
    max_turns: int = 8,
    env: Mapping[str, str] | None = None,
    working_dir: Path | None = None,
    harness: Harness | None = None,
    input_func: Callable[[str], str] = input,
    output: TextIO | None = None,
) -> int:
    """Build (or accept) a Harness and run ``session_loop`` to completion.

    Returns 0 on clean exit, 2 when required env vars are missing and no
    ``harness`` was injected (keeps tests deterministic without env).
    """
    import os as _os

    out = output if output is not None else sys.stdout
    if harness is None:
        env_map = env if env is not None else _os.environ
        if _missing(env_map):
            out.write("missing env: " + ", ".join(_missing(env_map)) + "\n")
            return 2
        harness = build_default_harness(
            env=env_map,
            working_dir=working_dir or Path.cwd(),
            max_turns=max_turns,
        )

    sink = EventSink(events_path)
    sink.emit(
        "session.repl.started",
        events_path=str(events_path),
        model=model,
    )
    options = SessionOptions(model=model, system_prompt=system, max_turns=max_turns)

    async def _run(harness: Harness) -> None:
        async with harness:
            session = await harness.start_session(options)
            await session_loop(
                session=session,
                sink=sink,
                input_func=input_func,
                output=out,
            )

    asyncio.run(_run(harness))
    sink.emit("session.repl.stopped")
    return 0


__all__ = [
    "build_default_harness",
    "handle_event",
    "run_session_repl",
    "session_loop",
]
