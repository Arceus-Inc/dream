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
from dataclasses import fields
from pathlib import Path
from typing import Any, TextIO

from dream.config.paths import DreamPaths
from dream.contracts.provider import ProviderCapabilities
from dream.engine._adapter_openai import (
    OpenAIChatStreamer,
    httpx_chat_completion_stream,
)
from dream.engine._engine import QueryEngine, build_query_engine
from dream.engine._permission_gate import make_permission_gate
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
from dream.mcp import McpClientManager
from dream.observability import JsonlTracer, TraceWriter
from dream.permissions import SessionLimits
from dream.repl._events import EventSink
from dream.repl._mcp import mcp_paths, setup_mcp_session
from dream.repl._runtime_info import render_runtime_info
from dream.services import cron as cron_service
from dream.services.compact._orchestrator import (
    AutoCompactState,
    auto_compact_if_needed,
)
from dream.services.context_log import ContextEvent
from dream.services.core_beliefs import extract_standing_orders, render_standing_orders
from dream.services.repo_validator import has_blocking
from dream.services.threat_scan import threat_scan
from dream.services.token_estimation import (
    estimate_conversation_tokens,
    utilisation,
)
from dream.session import Session, SessionOptions
from dream.skills import (
    SKILL_CONTEXT_KEY,
    SkillContext,
    SkillRegistry,
    build_session_skill_registry,
    render_skill_catalogue,
    validate_skills,
)
from dream.tasks import (
    TASK_CONTEXT_KEY,
    BackgroundTaskManager,
    TaskRecord,
    TaskSessionContext,
)
from dream.tasks._cron import CRON_MANIFEST_DIR, load_cron_manifests
from dream.tools._registry import ToolRegistry
from dream.tools.builtin import default_registry

# A context-event sink the skill registry calls when a body loads.
SkillEventSink = Callable[[ContextEvent], None]

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
) -> Harness:
    """Build a Harness whose ``_engine_factory`` produces a real engine.

    Reads ``DREAM_SMOKE_API_KEY``, ``DREAM_SMOKE_MODEL`` and
    ``DREAM_SMOKE_BASE_URL`` from ``env``. Each ``start_session`` call
    constructs a fresh ``OpenAIChatStreamer`` so per-session
    ``system_prompt`` / ``model`` overrides take effect; the
    ``ToolRegistry`` and ``AutoCompactState`` are shared so registrations
    and compaction-cooldown state survive across sessions in the same
    REPL process.

    ``registry`` may be supplied so the caller can register additional tools
    (e.g. MCP, Spec 06 slice 4) into the same registry *after* this returns but
    *before* the first session starts — the tool wire-schema and the skill
    available-tool set are computed lazily per session, so late registrations
    are reflected.
    """
    missing = _missing(env)
    if missing:
        raise KeyError("missing required env vars: " + ", ".join(missing))
    api_key = env["DREAM_SMOKE_API_KEY"]
    model = env["DREAM_SMOKE_MODEL"]
    base_url = env.get("DREAM_SMOKE_BASE_URL", "https://api.openai.com/v1")
    tool_registry = registry if registry is not None else default_registry()
    compactor = AutoCompactState()
    paths = DreamPaths(repo=working_dir, home=Path.home()).ensure()
    # Task tools (Spec 07): one BackgroundTaskManager per harness, shared across
    # sessions in this REPL so task IDs / archives stay consistent. The cron
    # registry lives at the in-repo convention (``.dream/cron/registry.json``);
    # the exec-plans root is the parent of ``exec_plans_active`` since the FSM
    # appends the state segment itself via :func:`plan_dir`.
    task_manager = BackgroundTaskManager(tasks_dir=paths.tasks_dir)
    task_context = TaskSessionContext(
        manager=task_manager,
        cron_registry_path=paths.dream_dir / "cron" / "registry.json",
        plans_root=paths.exec_plans_active.parent,
    )
    # Spec 07 trigger surface: ensure the four default cron kinds exist on
    # disk (``.harness/cron/*.toml``) and that any present manifest is
    # registered in the durable registry. Both calls are idempotent so
    # operator edits to either the manifest or the registry survive
    # restart.
    cron_service.bootstrap_default_manifests(working_dir)
    cron_service.ensure_registry_seeded(
        task_context.cron_registry_path,
        load_cron_manifests(Path(working_dir) / CRON_MANIFEST_DIR),
    )
    # 128K is the default we use throughout Spec 02; the watch panel /
    # /util command surface utilisation against this number.
    capabilities = ProviderCapabilities(max_context_tokens=128_000)

    # Skills (Spec 06 slice 2): the frontmatter catalogue goes into the system
    # prompt so the model can discover skills; the SkillContext rides the
    # dispatcher's context_metadata so the `skill` tool can load bodies.
    catalogue = (
        render_skill_catalogue(skill_registry.list_meta()) if skill_registry else ""
    )
    # Runtime environment (shell + OS + python) injected so the model picks the
    # right command syntax when it calls ``task_create command=...`` — without
    # this it guesses bash on Windows and cmd.exe rejects the command.
    runtime_info = render_runtime_info(env=env, working_dir=working_dir)

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        # Render the registry into OpenAI ``tools`` wire shape per session (cheap;
        # a handful of tools) so tools registered after build — MCP adapters /
        # resource + auth tools — are visible to the model. The engine's
        # TurnStreamer Protocol has no tools parameter (only messages), so we
        # smuggle the schema through ``httpx_chat_completion_stream``'s
        # ``extra_params`` — splatted verbatim into every request body.
        tools = tool_registry.list_tools()
        tools_wire: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema(),
                },
            }
            for t in tools
        ]
        # Built per session too, so the available-tool set the `skill` tool
        # checks ``tools_required`` against includes late (MCP) registrations.
        skill_context = (
            SkillContext(
                registry=skill_registry,
                available_tools=frozenset(t.name for t in tools),
                event_sink=skill_event_sink,
            )
            if skill_registry is not None
            else None
        )
        # System prompt assembly order: the governance standing orders FIRST
        # (the constitution outranks everything; Spec 13F AC #21-22, re-extracted
        # every session start), then runtime info (host facts the model must
        # trust), the skill catalogue (capabilities), and the caller-supplied
        # prompt (task framing). Each block survives if the next is empty.
        standing_orders = render_standing_orders(
            extract_standing_orders(paths.repo / "docs" / "design-docs" / "core-beliefs.md")
        )
        parts = [standing_orders] if standing_orders else []
        parts.append(runtime_info)
        if catalogue:
            parts.append(catalogue)
        if options.system_prompt:
            parts.append(options.system_prompt)
        system_prompt = "\n\n".join(parts)
        streamer = OpenAIChatStreamer(
            stream_chat_completion=httpx_chat_completion_stream(
                api_key=api_key,
                base_url=base_url,
                extra_params={"tools": tools_wire, "tool_choice": "auto"}
                if tools_wire
                else None,
            ),
            model=options.model or model,
            system_prompt=system_prompt,
        )
        # OTel-shaped trace (Spec 12a): one durable JSONL per session under the
        # task sidecar. The session_id doubles as the sidecar dir key in the REPL.
        tracer = JsonlTracer(
            TraceWriter(DreamPaths(repo=working_dir, home=Path.home()).trace_log(session_id)),
            session_id=session_id,
            task_id=session_id,
        )
        # Spec 13C: gate every tool call against the sandbox policy assembled
        # from the registry's declared tiers + operator .harness config. Stale
        # promotions etc. surface as warnings (data); not emitted here yet.
        permission_gate, _gate_warnings = make_permission_gate(
            tool_registry, paths=paths, cwd=working_dir
        )
        return build_query_engine(
            streamer=streamer,
            registry=tool_registry,
            session_id=session_id,
            working_dir=working_dir,
            max_turns=options.max_turns or max_turns,
            permission_gate=permission_gate,
            limits=SessionLimits(),
            context_metadata=_build_context_metadata(
                skill_context=skill_context, task_context=task_context
            ),
            compactor=compactor,
            compaction_capabilities=capabilities,
            tracer=tracer,
            model=options.model or model,
        )

    # Stash the task manager on the harness config so the REPL can register
    # lifecycle listeners and surface cron-spawned task starts/completions
    # alongside ordinary tool calls. ``extra`` is the documented escape hatch
    # for harness-bound subsystems the SDK Harness API doesn't model yet.
    # The cron registry path rides alongside so the in-process scheduler
    # tick loop (started by ``run_session_repl``) knows where to poll.
    config = HarnessConfig(working_dir=working_dir, _engine_factory=_factory)
    config.extra["task_manager"] = task_manager
    config.extra["cron_registry_path"] = task_context.cron_registry_path
    return Harness(config)


def _build_context_metadata(
    *, skill_context: SkillContext | None, task_context: TaskSessionContext
) -> dict[str, Any]:
    """Merge skill + task contexts into the dispatcher's ``context_metadata``."""
    metadata: dict[str, Any] = {TASK_CONTEXT_KEY: task_context}
    if skill_context is not None:
        metadata[SKILL_CONTEXT_KEY] = skill_context
    return metadata


# ---------------------------------------------------------------------------
# Visual styling (TTY-gated, dependency-free ANSI)
# ---------------------------------------------------------------------------
#
# Colour is opt-out: enabled only when ``output`` is a real terminal. Tests
# inject ``io.StringIO`` (no ``isatty``), so they continue to see plain text
# and the strict-equality checks in test_session_repl.py keep passing.

_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_CYAN = "\x1b[36m"
_MAGENTA = "\x1b[35m"

_TOOL_RESULT_LIMIT = 160
_TOOL_INPUT_LIMIT = 200


def _use_colour(output: TextIO) -> bool:
    isatty = getattr(output, "isatty", None)
    return bool(isatty and isatty())


def _c(code: str, text: str, *, use: bool) -> str:
    """Wrap ``text`` in ``code`` + reset, or return as-is when ``use`` is False."""
    if not use or not code:
        return text
    return f"{code}{text}{_RESET}"


def _flatten(s: str) -> str:
    """Collapse newlines/tabs so a tool blob renders on one tidy line."""
    return s.replace("\r", "").replace("\n", " \u23ce ").replace("\t", "  ")


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

    ``TextDelta`` is a raw passthrough by contract — never decorated — so
    streaming model prose stays clean and ``out.getvalue() == ev.text``
    in tests.
    """
    use = _use_colour(output)
    if isinstance(ev, TextDelta):
        output.write(ev.text)
        output.flush()
        sink.emit("session.text_delta", text=ev.text)
    elif isinstance(ev, ToolUseStart):
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
    elif isinstance(ev, ToolUseResult):
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
    elif isinstance(ev, TurnComplete):
        usage_str = " ".join(f"{k}={v}" for k, v in ev.usage.items())
        line = f"\u2500\u2500 turn \u00b7 {ev.stop_reason}"
        if usage_str:
            line += f" \u00b7 {usage_str}"
        line += " \u2500\u2500"
        output.write("\n" + _c(_DIM, line, use=use) + "\n")
        sink.emit(
            "session.turn_complete",
            stop_reason=ev.stop_reason,
            usage=dict(ev.usage),
        )
    elif isinstance(ev, Compacted):
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
    elif isinstance(ev, Error):
        output.write(
            _c(_RED, f"\u2717 error \u00b7 {ev.code} \u00b7 {ev.message}", use=use) + "\n"
        )
        sink.emit("session.error", code=ev.code, message=ev.message)


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
    defn = registry.use_skill(
        name, event_sink=lambda ev: _emit_context_event(sink, ev)
    )
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
    if cmd == "/skills":
        _cmd_skills(skill_registry, output=output, use=use)
        sink.emit("session.skills_listed")
        return True
    if cmd == "/skill":
        _cmd_skill(arg, skill_registry, sink=sink, output=output, use=use)
        return True
    if cmd == "/mcp":
        _cmd_mcp(mcp_manager, output=output, use=use)
        sink.emit("session.mcp_listed")
        return True
    if cmd in ("/help", "/?"):
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
        output.write(_c(_CYAN, "commands", use=use) + "\n")
        for name, desc in commands:
            output.write(
                "  "
                + _c(_BOLD, f"{name:<10}", use=use)
                + _c(_DIM, desc, use=use)
                + "\n"
            )
        output.write(_c(_DIM, "anything else is sent to the model.", use=use) + "\n")
        return True
    if cmd == "/info":
        output.write(
            _c(_CYAN, "session", use=use) + "\n"
            + "  " + _c(_DIM, "id    ", use=use) + session.id + "\n"
            + "  " + _c(_DIM, "model ", use=use)
            + (session.options.model or "<default>") + "\n"
            + "  " + _c(_DIM, "cost  ", use=use)
            + f"in={session.cost.input_tokens} out={session.cost.output_tokens}\n"
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
        output.write(
            _c(tone, f"util {pct:5.1f}%", use=use)
            + "  "
            + _c(tone, bar, use=use)
            + "  "
            + _c(_DIM, f"messages={len(session._transcript)}", use=use)
            + "  "
            + _c(_DIM, f"cost in={cost.input_tokens} out={cost.output_tokens}", use=use)
            + "\n"
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
            output.write(
                _c(_YELLOW, "\u25cb compact \u00b7 no compactor wired on this engine", use=use)
                + "\n"
            )
            sink.emit("session.compact_skipped", reason="no_compactor")
            return True
        capabilities = engine.compaction_capabilities
        threshold = engine.compaction_threshold
        preserve_recent = engine.compaction_preserve_recent
        pre_count = len(session._transcript)
        pre_tokens = estimate_conversation_tokens(session._transcript)
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
        post_tokens = estimate_conversation_tokens(new_transcript)
        # ``force=True`` means ``result`` is effectively never ``None``, so we
        # cannot use it to detect a no-op. Instead compare the real pre/post
        # deltas: a compaction that reclaimed neither messages nor tokens did
        # nothing (e.g. transcript already minimal / nothing compactable).
        if result is None or (removed == 0 and post_tokens >= pre_tokens):
            output.write(
                _c(_DIM, "\u25cb compact \u00b7 nothing to compact", use=use) + "\n"
            )
            sink.emit("session.compact_skipped", reason="noop")
            return True
        post_util = utilisation(new_transcript, capabilities)
        output.write(
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
    if cmd == "/reset":
        # Spec 05 Session has no public reset hook yet, so we just drop
        # the in-memory transcript via the private attribute. The next
        # send starts from a clean slate while keeping the engine and
        # cost counters intact.
        session._transcript.clear()
        output.write(_c(_CYAN, "\u21bb transcript cleared", use=use) + "\n")
        sink.emit("session.reset", session_id=session.id)
        return True
    output.write(
        _c(_YELLOW, f"unknown command {cmd!r}", use=use)
        + _c(_DIM, " \u00b7 /help for list", use=use)
        + "\n"
    )
    return True


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
    work_dir = working_dir or Path.cwd()

    # Session-start skill gate (Spec 06 MUST #3): a malformed SKILL.md blocks
    # the session before anything else. Runs even with an injected harness.
    skill_findings = validate_skills(work_dir)
    if has_blocking(skill_findings):
        for finding in skill_findings:
            out.write(f"blocked: {finding.message} ({finding.path})\n")
        return 3

    # Session-start threat scan (Spec 13E): a worktree secret / world-writable
    # file under docs/ / eval-in-tool in .harness/tools/ blocks the session
    # before the agent runs. (The spec-01 structural validator is not wired
    # here yet; this gates the security findings only.)
    threat_findings = threat_scan(DreamPaths(repo=work_dir, home=Path.home()))
    if has_blocking(threat_findings):
        for finding in threat_findings:
            out.write(f"blocked: {finding.message} ({finding.path})\n")
        return 3

    skill_registry, shadows = build_session_skill_registry(work_dir)
    for shadow in shadows:
        out.write(
            f"note: skill {shadow.name!r} from {shadow.winner_source} "
            f"shadows {shadow.shadowed_source}\n"
        )

    sink = EventSink(events_path)

    def _skill_event_sink(event: ContextEvent) -> None:
        _emit_context_event(sink, event)

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
        )

    sink.emit(
        "session.repl.started",
        events_path=str(events_path),
        model=model,
    )
    options = SessionOptions(model=model, system_prompt=system, max_turns=max_turns)
    allowlist_path, credentials_path = mcp_paths(work_dir)

    async def _run(harness: Harness) -> int:
        mcp_manager: McpClientManager | None = None
        # Surface background task lifecycle (cron firings + ad-hoc task_create
        # spawns) inline in the REPL, the same way tool calls are rendered.
        # The harness stashes its task_manager on ``config.extra`` so the REPL
        # can subscribe without the SDK Harness API exposing every subsystem.
        unsubs: list[Callable[[], None]] = []
        cron_task: asyncio.Task[None] | None = None
        task_manager = harness.config.extra.get("task_manager")
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
            # Spec 07 in-process scheduler tick — polls the registry every
            # ``DEFAULT_POLL_SECONDS`` and fires any due cron job. Lives only
            # for the duration of this REPL session; an OS-level trigger
            # (``python -m dream.repl cron run <kind>``) covers the
            # cron-without-an-open-REPL case.
            cron_registry = harness.config.extra.get("cron_registry_path")
            if isinstance(cron_registry, Path):
                cron_task = asyncio.create_task(
                    cron_service.cron_tick_loop(
                        manager=task_manager,
                        working_dir=harness.config.working_dir,
                        registry_path=cron_registry,
                    ),
                    name="cron-tick-loop",
                )
        async with harness:
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
                if cron_task is not None:
                    cron_task.cancel()
                    try:
                        await cron_task
                    except (asyncio.CancelledError, Exception):
                        pass
                for un in unsubs:
                    un()
        return 0

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
