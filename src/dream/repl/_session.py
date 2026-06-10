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
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TextIO

from dream._factory import (
    DEFAULT_BASE_URL,
    PolicyWarningSink,
    SkillEventSink,
    build_harness,
)
from dream.config.paths import DreamPaths
from dream.events import (
    Compacted,
    Error,
    Event,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
    TurnComplete,
)
from dream.harness import Harness
from dream.mcp import McpClientManager
from dream.repl._ansi import (
    BOLD as _BOLD,
)
from dream.repl._ansi import (
    CYAN as _CYAN,
)
from dream.repl._ansi import (
    DIM as _DIM,
)
from dream.repl._ansi import (
    GREEN as _GREEN,
)
from dream.repl._ansi import (
    MAGENTA as _MAGENTA,
)
from dream.repl._ansi import (
    RED as _RED,
)
from dream.repl._ansi import (
    YELLOW as _YELLOW,
)
from dream.repl._ansi import c as _c
from dream.repl._ansi import flatten as _flatten
from dream.repl._ansi import use_colour as _use_colour
from dream.repl._events import EventSink
from dream.repl._mcp import mcp_paths, setup_mcp_session
from dream.runtime import (
    Runtime,
    RuntimeBusyError,
    RuntimeConfig,
    run_boot_gates,
)
from dream.services.compact._orchestrator import (
    auto_compact_if_needed,
)
from dream.services.context_log import ContextEvent
from dream.services.repo_validator import has_blocking
from dream.services.token_estimation import (
    estimate_conversation_tokens,
    utilisation,
)
from dream.session import Session, SessionOptions
from dream.skills import (
    SkillRegistry,
    build_session_skill_registry,
)
from dream.tasks import (
    BackgroundTaskManager,
    TaskRecord,
)
from dream.tools._registry import ToolRegistry
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
    registry: ToolRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
    skill_event_sink: SkillEventSink | None = None,
    policy_warning_sink: PolicyWarningSink | None = None,
) -> Harness:
    """REPL convenience over :func:`dream.build_harness`, credentials from env.

    Reads ``DREAM_SMOKE_API_KEY``, ``DREAM_SMOKE_MODEL`` and (optionally)
    ``DREAM_SMOKE_BASE_URL`` from ``env`` and delegates everything else —
    engine wiring, tools, task/cron bootstrap, skills, tracing — to the
    public factory. Raises ``KeyError`` naming any missing required vars so
    the CLI failure mode stays explicit.
    """
    missing = _missing(env)
    if missing:
        raise KeyError("missing required env vars: " + ", ".join(missing))
    return build_harness(
        model=env["DREAM_SMOKE_MODEL"],
        api_key=env["DREAM_SMOKE_API_KEY"],
        base_url=env.get("DREAM_SMOKE_BASE_URL", DEFAULT_BASE_URL),
        working_dir=working_dir,
        max_turns=max_turns,
        registry=registry,
        skill_registry=skill_registry,
        skill_event_sink=skill_event_sink,
        policy_warning_sink=policy_warning_sink,
        env=env,
    )


# ---------------------------------------------------------------------------
# Visual styling (TTY-gated, dependency-free ANSI)
# ---------------------------------------------------------------------------
#
# Colour is opt-out: enabled only when ``output`` is a real terminal. Tests
# inject ``io.StringIO`` (no ``isatty``), so they continue to see plain text
# and the strict-equality checks in test_session_repl.py keep passing.

# ANSI constants + the TTY gate / wrap / flatten primitives are imported at
# module top from the shared :mod:`dream.repl._ansi` module (aliased under the
# ``_NAME`` spelling every renderer already uses). The REPL gate does *not*
# honour ``NO_COLOR`` (only a real ``isatty`` toggles colour) \u2014 preserved via
# the default ``respect_no_color=False``.

_TOOL_RESULT_LIMIT = 160
_TOOL_INPUT_LIMIT = 200


# ---------------------------------------------------------------------------
# Background-task lifecycle rendering (cron + ad-hoc task_create)
# ---------------------------------------------------------------------------


def _task_label(task: TaskRecord) -> str:
    """A compact ``cron:<kind> <task_id>`` / ``task <task_id>`` label.

    Cron-spawned tasks carry ``cron.kind`` / ``cron.run_id`` in metadata
    (see :func:`dream.tasks._cron_session.spawn_cron_session`); ad-hoc
    ``task_create`` calls don't. Splitting the label this way lets the
    user spot cron firings at a glance.
    """
    kind = task.metadata.get("cron.kind") if task.metadata else None
    if kind:
        run_id = task.metadata.get("cron.run_id") or task.id
        return f"cron:{kind} {run_id}"
    return f"task {task.id}"


def render_task_started(
    task: TaskRecord, *, sink: EventSink, output: TextIO
) -> None:
    """Render a background task's spawn to ``output`` + JSONL sink.

    Mirrors the ``ToolUseStart`` shape (``▸ name args``) so cron and
    background task lifecycle reads the same as ordinary tool calls.
    """
    use = _use_colour(output)
    output.write(
        "\n"
        + _c(_MAGENTA, "  \u25b8 ", use=use)
        + _c(_BOLD, _task_label(task), use=use)
        + "  "
        + _c(_DIM, _flatten(task.description), use=use)
        + "\n"
    )
    output.flush()
    sink.emit(
        "session.task_started",
        task_id=task.id,
        task_type=task.type,
        description=task.description,
        metadata=dict(task.metadata) if task.metadata else {},
    )


def render_task_finished(
    task: TaskRecord, *, sink: EventSink, output: TextIO
) -> None:
    """Render a background task's terminal transition (completed/failed/killed)."""
    use = _use_colour(output)
    status = task.status
    colour = _GREEN if status == "completed" else _RED
    rc = task.return_code if task.return_code is not None else "?"
    duration = ""
    if task.started_at and task.ended_at:
        duration = f"  {task.ended_at - task.started_at:.1f}s"
    output.write(
        _c(colour, "  \u21b3 ", use=use)
        + _c(_BOLD, _task_label(task), use=use)
        + "  "
        + _c(colour, f"{status} (rc={rc}){duration}", use=use)
        + "\n"
    )
    output.flush()
    sink.emit(
        "session.task_finished",
        task_id=task.id,
        task_type=task.type,
        status=status,
        return_code=task.return_code,
    )


# ---------------------------------------------------------------------------
# Per-event rendering
# ---------------------------------------------------------------------------


def handle_event(ev: Event, *, sink: EventSink, output: TextIO) -> None:
    """Render one public ``Event`` to ``output`` + JSONL sink.

    The JSONL discriminator namespace is ``session.*`` for the engine's
    own events and ``context.compaction.completed`` for ``Compacted`` so
    the REPL #3 watch-panel colour table can route it alongside the
    Spec 04 context-log events.

    Dispatches on the event's concrete type via :data:`_EVENT_RENDERERS`;
    each renderer writes the styled line and mirrors the event to the sink.
    Unmapped event types (orchestration internals) are dropped silently.
    ``TextDelta`` is a raw passthrough by contract — never decorated.
    """
    renderer = _EVENT_RENDERERS.get(type(ev))
    if renderer is None:
        return
    renderer(ev, sink=sink, output=output, use=_use_colour(output))


def _render_text_delta(ev: TextDelta, *, sink: EventSink, output: TextIO, use: bool) -> None:
    # Raw passthrough by contract \u2014 never decorated \u2014 so streaming prose stays
    # clean and ``out.getvalue() == ev.text`` in tests.
    output.write(ev.text)
    output.flush()
    sink.emit("session.text_delta", text=ev.text)


def _render_tool_use_start(
    ev: ToolUseStart, *, sink: EventSink, output: TextIO, use: bool
) -> None:
    args_repr = _flatten(repr(ev.input))
    if len(args_repr) > _TOOL_INPUT_LIMIT:
        args_repr = args_repr[:_TOOL_INPUT_LIMIT] + "\u2026"
    output.write(
        "\n"
        + _c(_CYAN, "  \u25b8 ", use=use)
        + _c(_BOLD, ev.name, use=use)
        + "  "
        + _c(_DIM, args_repr, use=use)
        + "\n"
    )
    sink.emit(
        "session.tool_use_start",
        tool_use_id=ev.tool_use_id,
        name=ev.name,
        input=ev.input,
    )


def _render_tool_use_result(
    ev: ToolUseResult, *, sink: EventSink, output: TextIO, use: bool
) -> None:
    full_len = len(ev.content)
    body = ev.content if full_len <= _TOOL_RESULT_LIMIT else ev.content[:_TOOL_RESULT_LIMIT]
    snippet = _flatten(body)
    if full_len > _TOOL_RESULT_LIMIT:
        snippet += "\u2026"
    suffix = (
        _c(_DIM, f"  (+{full_len - _TOOL_RESULT_LIMIT} chars)", use=use)
        if full_len > _TOOL_RESULT_LIMIT
        else ""
    )
    if ev.is_error:
        head = _c(_RED, "  \u2717 " + ev.name + " failed", use=use)
    else:
        head = _c(_GREEN, "  \u2713", use=use) + " " + _c(_DIM, ev.name, use=use)
    output.write(f"{head}  {_c(_DIM, snippet, use=use)}{suffix}\n")
    sink.emit(
        "session.tool_use_result",
        tool_use_id=ev.tool_use_id,
        name=ev.name,
        is_error=ev.is_error,
        content_chars=len(ev.content),
    )


def _render_turn_complete(
    ev: TurnComplete, *, sink: EventSink, output: TextIO, use: bool
) -> None:
    usage_str = " ".join(f"{k}={v}" for k, v in ev.usage.items())
    line = f"\u2500\u2500 turn \u00b7 {ev.stop_reason}"
    if usage_str:
        line += f" \u00b7 {usage_str}"
    line += " \u2500\u2500"
    output.write("\n" + _c(_DIM, line, use=use) + "\n")
    sink.emit("session.turn_complete", stop_reason=ev.stop_reason, usage=dict(ev.usage))


def _render_compacted(ev: Compacted, *, sink: EventSink, output: TextIO, use: bool) -> None:
    output.write(
        _c(
            _CYAN,
            f"\u25c6 compacted \u00b7 removed {ev.removed_messages} msgs "
            f"\u00b7 {ev.summary_tokens} tokens",
            use=use,
        )
        + "\n"
    )
    sink.emit(
        "context.compaction.completed",
        removed_messages=ev.removed_messages,
        summary_tokens=ev.summary_tokens,
    )


def _render_error(ev: Error, *, sink: EventSink, output: TextIO, use: bool) -> None:
    output.write(
        _c(_RED, f"\u2717 error \u00b7 {ev.code} \u00b7 {ev.message}", use=use) + "\n"
    )
    sink.emit("session.error", code=ev.code, message=ev.message)


# Public event type \u2192 its renderer. Unmapped event types are dropped silently
# (the prior ``if/elif`` ladder had no ``else`` branch either).
_EVENT_RENDERERS: dict[type[Event], Callable[..., None]] = {
    TextDelta: _render_text_delta,
    ToolUseStart: _render_tool_use_start,
    ToolUseResult: _render_tool_use_result,
    TurnComplete: _render_turn_complete,
    Compacted: _render_compacted,
    Error: _render_error,
}


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def _emit_context_event(sink: EventSink, event: ContextEvent) -> None:
    """Adapt a context-log event onto the JSONL ``EventSink`` (returns None)."""
    sink.emit(event.name, **{f.name: getattr(event, f.name) for f in fields(event)})


def _cmd_skills(
    registry: SkillRegistry | None, *, output: TextIO, use: bool
) -> None:
    """Print the skill catalogue (frontmatter only), or a hint if none."""
    metas = registry.list_meta() if registry is not None else []
    if not metas:
        output.write(_c(_DIM, "no skills registered", use=use) + "\n")
        return
    output.write(_c(_CYAN, "skills", use=use) + "\n")
    for meta in metas:
        flags = " (user-only)" if meta.disable_model_invocation else ""
        output.write(
            "  "
            + _c(_BOLD, f"{meta.name}", use=use)
            + _c(_DIM, f" [{meta.source}]{flags} · {meta.description}", use=use)
            + "\n"
        )


def _cmd_skill(
    name: str,
    registry: SkillRegistry | None,
    *,
    sink: EventSink,
    output: TextIO,
    use: bool,
) -> None:
    """Operator-load a skill body (operators may load user-only skills)."""
    if not name:
        output.write(_c(_YELLOW, "usage: /skill <name>", use=use) + "\n")
        return
    if registry is None or registry.resolve(name) is None:
        output.write(_c(_YELLOW, f"unknown skill {name!r}", use=use) + "\n")
        return
    # Operator invocation: ``disable_model_invocation`` gates the *model*, not
    # the operator, so we load the body here regardless of that flag.
    #
    # The body is read lazily from disk, so it can fail *after* startup: the
    # SKILL.md may have been removed, become unreadable, hold undecodable bytes,
    # or have malformed frontmatter. None of those should crash the REPL command
    # path — surface the failure and keep the session loop alive. ``OSError``
    # covers removal/IO; ``ValueError`` covers ``UnicodeDecodeError`` and
    # ``SkillFrontmatterError`` (both subclasses).
    try:
        defn = registry.use_skill(
            name, event_sink=lambda ev: _emit_context_event(sink, ev)
        )
    except (OSError, ValueError) as exc:
        output.write(
            _c(_RED, f"could not load skill {name!r}: {exc}", use=use) + "\n"
        )
        sink.emit("session.skill_load_failed", skill=name, error=str(exc))
        return
    output.write(_c(_CYAN, f"skill: {defn.meta.name}", use=use) + "\n")
    output.write(defn.content.rstrip() + "\n")


def _cmd_mcp(
    manager: McpClientManager | None, *, output: TextIO, use: bool
) -> None:
    """Print per-server MCP connection status, or a hint if MCP is absent."""
    statuses = manager.list_statuses() if manager is not None else []
    if not statuses:
        output.write(_c(_DIM, "no MCP servers configured", use=use) + "\n")
        return
    output.write(_c(_CYAN, "mcp servers", use=use) + "\n")
    for status in statuses:
        tone = _GREEN if status.state == "connected" else _YELLOW
        detail = f" · {status.detail}" if status.detail else ""
        output.write(
            "  "
            + _c(_BOLD, status.name, use=use)
            + _c(tone, f" [{status.state}]", use=use)
            + _c(
                _DIM,
                f" {status.transport} · tools={len(status.tools)} "
                f"resources={len(status.resources)}{detail}",
                use=use,
            )
            + "\n"
        )


@dataclass
class _SlashCtx:
    """Everything a session slash-command handler may need (built per call)."""

    arg: str
    session: Session
    sink: EventSink
    output: TextIO
    use: bool
    skill_registry: SkillRegistry | None
    mcp_manager: McpClientManager | None


def _slash_skills(ctx: _SlashCtx) -> bool:
    _cmd_skills(ctx.skill_registry, output=ctx.output, use=ctx.use)
    ctx.sink.emit("session.skills_listed")
    return True


def _slash_skill(ctx: _SlashCtx) -> bool:
    _cmd_skill(ctx.arg, ctx.skill_registry, sink=ctx.sink, output=ctx.output, use=ctx.use)
    return True


def _slash_mcp(ctx: _SlashCtx) -> bool:
    _cmd_mcp(ctx.mcp_manager, output=ctx.output, use=ctx.use)
    ctx.sink.emit("session.mcp_listed")
    return True


def _slash_help(ctx: _SlashCtx) -> bool:
    commands = [
        ("/help", "this list"),
        ("/info", "session id, model, running cost"),
        ("/util", "context utilisation % + cost"),
        ("/compact", "force a Spec 04 microcompact now"),
        ("/skills", "list available skills (frontmatter)"),
        ("/skill <name>", "load a skill body (operator)"),
        ("/mcp", "list MCP servers + connection status"),
        ("/reset", "clear transcript (keep engine + cost)"),
        ("/quit", "leave the REPL"),
    ]
    out, use = ctx.output, ctx.use
    out.write(_c(_CYAN, "commands", use=use) + "\n")
    for name, desc in commands:
        out.write("  " + _c(_BOLD, f"{name:<10}", use=use) + _c(_DIM, desc, use=use) + "\n")
    out.write(_c(_DIM, "anything else is sent to the model.", use=use) + "\n")
    return True


def _slash_info(ctx: _SlashCtx) -> bool:
    session, use = ctx.session, ctx.use
    ctx.output.write(
        _c(_CYAN, "session", use=use) + "\n"
        + "  " + _c(_DIM, "id    ", use=use) + session.id + "\n"
        + "  " + _c(_DIM, "model ", use=use)
        + (session.options.model or "<default>") + "\n"
        + "  " + _c(_DIM, "cost  ", use=use)
        + f"in={session.cost.input_tokens} out={session.cost.output_tokens}\n"
    )
    ctx.sink.emit("session.info", session_id=session.id)
    return True


def _slash_util(ctx: _SlashCtx) -> bool:
    session, out, use = ctx.session, ctx.output, ctx.use
    # Read capabilities off the bound engine via the public accessor; if absent
    # we still render a usable line (utilisation falls back to 0.0).
    settings = session.compaction_settings()
    capabilities = settings.capabilities if settings is not None else None
    transcript = session.transcript
    pct = utilisation(transcript, capabilities) * 100.0
    cost = session.cost
    # Pressure-aware colouring: green < 50%, yellow 50-80%, red > 80%.
    if pct >= 80:
        tone = _RED
    elif pct >= 50:
        tone = _YELLOW
    else:
        tone = _GREEN
    bar_width = 20
    filled = max(0, min(bar_width, round(pct / 100.0 * bar_width)))
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    out.write(
        _c(tone, f"util {pct:5.1f}%", use=use)
        + "  "
        + _c(tone, bar, use=use)
        + "  "
        + _c(_DIM, f"messages={len(transcript)}", use=use)
        + "  "
        + _c(_DIM, f"cost in={cost.input_tokens} out={cost.output_tokens}", use=use)
        + "\n"
    )
    ctx.sink.emit(
        "session.util",
        session_id=session.id,
        utilisation=pct / 100.0,
        messages=len(transcript),
        input_tokens=cost.input_tokens,
        output_tokens=cost.output_tokens,
    )
    return True


def _slash_compact(ctx: _SlashCtx) -> bool:
    session, out, use, sink = ctx.session, ctx.output, ctx.use, ctx.sink
    settings = session.compaction_settings()
    if settings is None or settings.compactor is None:
        out.write(
            _c(_YELLOW, "\u25cb compact \u00b7 no compactor wired on this engine", use=use)
            + "\n"
        )
        sink.emit("session.compact_skipped", reason="no_compactor")
        return True
    capabilities = settings.capabilities
    transcript = session.transcript
    pre_count = len(transcript)
    pre_tokens = estimate_conversation_tokens(transcript)
    new_transcript, result = auto_compact_if_needed(
        transcript,
        capabilities=capabilities,
        state=settings.compactor,
        trigger="manual",
        threshold=settings.threshold,
        preserve_recent=settings.preserve_recent,
        force=True,
    )
    transcript[:] = new_transcript
    removed = max(0, pre_count - len(new_transcript))
    post_tokens = estimate_conversation_tokens(new_transcript)
    # ``force=True`` means ``result`` is effectively never ``None``, so we
    # cannot use it to detect a no-op. Instead compare the real pre/post
    # deltas: a compaction that reclaimed neither messages nor tokens did
    # nothing (e.g. transcript already minimal / nothing compactable).
    if result is None or (removed == 0 and post_tokens >= pre_tokens):
        out.write(_c(_DIM, "\u25cb compact \u00b7 nothing to compact", use=use) + "\n")
        sink.emit("session.compact_skipped", reason="noop")
        return True
    post_util = utilisation(new_transcript, capabilities)
    out.write(
        _c(
            _CYAN,
            f"\u25c6 compact \u00b7 tier={result.tier} "
            f"removed={removed} util_after={post_util * 100.0:.1f}%",
            use=use,
        )
        + "\n"
    )
    sink.emit(
        "context.compaction.completed",
        tier=result.tier,
        removed_messages=removed,
        resulting_utilisation=post_util,
    )
    return True


def _slash_reset(ctx: _SlashCtx) -> bool:
    # The next send starts from a clean slate while keeping the engine and
    # cost counters intact.
    ctx.session.transcript.clear()
    ctx.output.write(_c(_CYAN, "\u21bb transcript cleared", use=ctx.use) + "\n")
    ctx.sink.emit("session.reset", session_id=ctx.session.id)
    return True


# Command \u2192 handler. ``/quit`` / ``/exit`` are handled inline in
# ``_handle_slash`` because they alone return False (leave the loop); every
# handler here returns True (keep looping). ``/?`` is an alias of ``/help``.
_SLASH_COMMANDS: dict[str, Callable[[_SlashCtx], bool]] = {
    "/skills": _slash_skills,
    "/skill": _slash_skill,
    "/mcp": _slash_mcp,
    "/help": _slash_help,
    "/?": _slash_help,
    "/info": _slash_info,
    "/util": _slash_util,
    "/compact": _slash_compact,
    "/reset": _slash_reset,
}


def _handle_slash(
    line: str,
    *,
    session: Session,
    sink: EventSink,
    skill_registry: SkillRegistry | None = None,
    mcp_manager: McpClientManager | None = None,
    output: TextIO,
) -> bool:
    """Dispatch one slash command. Returns True to keep looping, False to quit."""
    use = _use_colour(output)
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/quit", "/exit"):
        return False
    handler = _SLASH_COMMANDS.get(cmd)
    if handler is None:
        output.write(
            _c(_YELLOW, f"unknown command {cmd!r}", use=use)
            + _c(_DIM, " \u00b7 /help for list", use=use)
            + "\n"
        )
        return True
    return handler(
        _SlashCtx(
            arg=arg,
            session=session,
            sink=sink,
            output=output,
            use=use,
            skill_registry=skill_registry,
            mcp_manager=mcp_manager,
        )
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def session_loop(
    *,
    session: Session,
    sink: EventSink,
    skill_registry: SkillRegistry | None = None,
    mcp_manager: McpClientManager | None = None,
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
    use = _use_colour(out)
    model = session.options.model or "<default>"
    sid = session.id[:8]
    # Two-line banner: a soft title rule + a hint line. Plain text in non-TTY.
    out.write(
        _c(_MAGENTA, "\u250c\u2500 dream", use=use)
        + _c(_DIM, f" \u00b7 session {sid} \u00b7 model={model}", use=use)
        + "\n"
        + _c(_MAGENTA, "\u2514\u2500 ", use=use)
        + _c(_DIM, "/help for commands \u00b7 /quit to exit", use=use)
        + "\n"
    )
    out.flush()

    while True:
        try:
            prompt = _c(_CYAN, "\u276f ", use=use) if use else "> "
            line = await asyncio.to_thread(input_func, prompt)
        except (EOFError, KeyboardInterrupt):
            out.write("\n")
            break
        if not line.strip():
            continue
        if line.startswith("/"):
            if not _handle_slash(
                line,
                session=session,
                sink=sink,
                skill_registry=skill_registry,
                mcp_manager=mcp_manager,
                output=out,
            ):
                break
            continue
        try:
            async for ev in session.send(line):
                handle_event(ev, sink=sink, output=out)
        except Exception as exc:
            out.write(
                _c(_RED, f"\u2717 turn failed \u00b7 {type(exc).__name__}: {exc}", use=use)
                + "\n"
            )
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
    ``harness`` was injected, 3 when a malformed skill or a blocking MCP finding
    (unlisted/malformed allowlist, version-pin mismatch) blocks the session.
    """
    import os as _os

    out = output if output is not None else sys.stdout
    # When ``working_dir`` is omitted, prefer the injected harness's configured
    # repo root over the process cwd (#36) so skill validation, the skill
    # registry, MCP allowlist, and the threat scan all target the active
    # session's repo — not whatever directory the operator launched from.
    if working_dir is not None:
        work_dir = working_dir
    elif harness is not None:
        work_dir = harness.config.working_dir
    else:
        work_dir = Path.cwd()

    # Resolve the home root from the effective env so ``DREAM_HOME`` overrides
    # are honoured here too (#43), keeping path roots consistent with the
    # harness ``build_default_harness`` constructs below.
    paths_env = env if env is not None else _os.environ
    session_paths = DreamPaths.resolve(work_dir, env=paths_env)

    # Boot gates (spec 15 P1): the sequencing lives in ``dream.runtime`` now —
    # skills gate (block), structural validation (warn), threat scan (block).
    # The REPL runs them pre-flight because it must know the verdict *before*
    # building the skill registry below; the verdict is then handed to the
    # Runtime so boot doesn't scan twice.
    boot_report = run_boot_gates(working_dir=work_dir, paths=session_paths)
    if boot_report.blocked:
        for finding in boot_report.blocking_findings():
            out.write(f"blocked: {finding.message} ({finding.path})\n")
        return 3
    for finding in boot_report.repo_findings:
        out.write(f"warning: repo: {finding.message} ({finding.path})\n")

    skill_registry, shadows = build_session_skill_registry(work_dir)
    for shadow in shadows:
        out.write(
            f"note: skill {shadow.name!r} from {shadow.winner_source} "
            f"shadows {shadow.shadowed_source}\n"
        )

    sink = EventSink(events_path)

    def _skill_event_sink(event: ContextEvent) -> None:
        _emit_context_event(sink, event)

    def _policy_warning_sink(message: str) -> None:
        # Operator-facing security signal (e.g. stale tier promotion): print it
        # and mirror it to the JSONL watch panel so it isn't silently lost (#47).
        out.write(f"warning: {message}\n")
        sink.emit("session.policy_warning", message=message)

    # MCP tools (Spec 06 slice 4) register into this shared registry inside the
    # event loop (connect is async); the harness reads the registry lazily per
    # session, so those late registrations are visible. Only when we build the
    # harness ourselves — an injected harness owns its own registry.
    tool_registry: ToolRegistry | None = None
    if harness is None:
        env_map = env if env is not None else _os.environ
        if _missing(env_map):
            out.write("missing env: " + ", ".join(_missing(env_map)) + "\n")
            return 2
        tool_registry = default_registry()
        harness = build_default_harness(
            env=env_map,
            working_dir=work_dir,
            max_turns=max_turns,
            registry=tool_registry,
            skill_registry=skill_registry,
            skill_event_sink=_skill_event_sink,
            policy_warning_sink=_policy_warning_sink,
        )

    sink.emit(
        "session.repl.started",
        events_path=str(events_path),
        model=model,
    )
    options = SessionOptions(model=model, system_prompt=system, max_turns=max_turns)
    allowlist_path, credentials_path = mcp_paths(work_dir)

    async def _run(harness: Harness) -> int:
        # The REPL is a *client* of the long-running runtime (spec 15 P1):
        # the Runtime owns the single-instance lock, the harness lifecycle,
        # the cron tick loop, task-lifecycle event mirroring, and drain. The
        # REPL adds its own pretty-rendering listeners on top and runs the
        # interactive session loop.
        runtime = Runtime(
            harness,
            RuntimeConfig(events_path=events_path),
            paths=session_paths,
            boot_report=boot_report,
        )
        try:
            await runtime.start()
        except RuntimeBusyError as exc:
            out.write(f"blocked: {exc}\n")
            return 3
        mcp_manager: McpClientManager | None = None
        # Surface background task lifecycle (cron firings + ad-hoc task_create
        # spawns) inline in the REPL, the same way tool calls are rendered.
        unsubs: list[Callable[[], None]] = []
        task_manager = harness.config.task_manager
        if isinstance(task_manager, BackgroundTaskManager):
            unsubs.append(
                task_manager.register_start_listener(
                    lambda t: render_task_started(t, sink=sink, output=out)
                )
            )
            unsubs.append(
                task_manager.register_completion_listener(
                    lambda t: render_task_finished(t, sink=sink, output=out)
                )
            )
        # Outer ``finally`` so listener unsubscription + runtime shutdown
        # ALWAYS run once the listeners are registered — including the early
        # ``return 3`` on a blocking MCP finding (#44). Keeping cleanup only in
        # the inner ``finally`` leaked sink/output references (and caused
        # duplicate lifecycle rendering on later runs) when MCP setup blocked.
        try:
            if tool_registry is not None:
                setup = await setup_mcp_session(
                    tool_registry,
                    allowlist_path=allowlist_path,
                    credentials_path=credentials_path,
                )
                if has_blocking(setup.findings):
                    for finding in setup.findings:
                        if finding.severity == "blocking":
                            out.write(f"blocked: {finding.message} ({finding.path})\n")
                    return 3
                mcp_manager = setup.manager
            try:
                session = await harness.start_session(options)
                await session_loop(
                    session=session,
                    sink=sink,
                    skill_registry=skill_registry,
                    mcp_manager=mcp_manager,
                    input_func=input_func,
                    output=out,
                )
            finally:
                if mcp_manager is not None:
                    await mcp_manager.close()
            return 0
        finally:
            for un in unsubs:
                un()
            await runtime.shutdown()

    # ``finally`` so the stop lifecycle event is written even when the loop
    # raises (otherwise an exception would skip ``session.repl.stopped`` and
    # leave the JSONL watch panel without a terminal event) (#38).
    try:
        code = asyncio.run(_run(harness))
    finally:
        sink.emit("session.repl.stopped")
    return code


__all__ = [
    "build_default_harness",
    "handle_event",
    "run_session_repl",
    "session_loop",
]
