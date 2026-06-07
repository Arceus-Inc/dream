"""Interactive REPL — exercises Substrate + CredentialPool + FailoverPolicy.

The REPL is the runnable "spec-complete" smoke test for Spec 02. It:

* builds one or more :class:`OpenAIChatSubstrate` adapters from env vars;
* wires them behind a :class:`CredentialPool` + :class:`FailoverPolicy`;
* runs a single-line input loop with slash commands;
* writes every meaningful event to a JSONL file the ``watch`` subcommand
  tails in a second terminal.

The substrate Protocol is single-prompt by design (Spec 02 §5). Multi-turn
chat is reconstructed REPL-side by flattening the in-memory message log
into one prompt with role headers — this is honest about Stage 2 limits;
the eventual Stage 03 turn FSM will pass a proper message list.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from dream.api.credentials import (
    Credential,
    CredentialPool,
    NoLiveCredential,
    Outcome,
)
from dream.api.failover import FailoverPolicy, NoLiveSubstrate
from dream.api.openai import OpenAIChatSubstrate
from dream.api.substrate import Substrate
from dream.repl._events import EventSink
from dream.repl._fake import FakeFailingSubstrate
from dream.tasks import (
    PLAN_STATES,
    BackgroundTaskManager,
    get_cron_job,
    load_cron_jobs,
    plan_dir,
    read_plan,
    set_job_enabled,
)
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry
from dream.tools.builtin import default_registry

# ---------------------------------------------------------------------------
# Substrate construction from env (no secrets in any file)
# ---------------------------------------------------------------------------


@dataclass
class SubstrateSpec:
    """One substrate the REPL knows how to build.

    Keys / base_url come from the environment to keep secrets out of source
    and out of any committed config.
    """

    name: str
    model: str
    base_url: str | None
    max_window: int
    timeout_seconds: float
    credentials: list[Credential]
    builder: Any  # callable (Credential) -> Substrate; ``Any`` to dodge protocol-typing noise


def _build_openai_substrate(spec: SubstrateSpec) -> Any:
    def _build(cred: Credential) -> Substrate:
        return OpenAIChatSubstrate(
            name=spec.name,
            api_key=cred.key,
            model=spec.model,
            base_url=spec.base_url,
            max_window_tokens=spec.max_window,
            timeout_seconds=spec.timeout_seconds,
        )

    return _build


def _env_int(name: str, default: int) -> int:
    """Parse an integer env var, with a clear error instead of a raw ValueError."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a valid integer") from exc


def _env_float(name: str, default: float) -> float:
    """Parse a float env var, with a clear error instead of a raw ValueError."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a valid number") from exc


def _spec_from_env() -> SubstrateSpec | None:
    """Build the primary substrate from ``DREAM_SMOKE_*`` env vars."""
    api_key = os.environ.get("DREAM_SMOKE_API_KEY")
    base_url = os.environ.get("DREAM_SMOKE_BASE_URL")
    model = os.environ.get("DREAM_SMOKE_MODEL")
    if not (api_key and model):
        return None
    name = os.environ.get("DREAM_SMOKE_NAME", "primary")
    label = os.environ.get("DREAM_SMOKE_LABEL", "env")
    spec = SubstrateSpec(
        name=name,
        model=model,
        base_url=base_url,
        max_window=_env_int("DREAM_SMOKE_MAX_WINDOW", 128_000),
        timeout_seconds=_env_float("DREAM_SMOKE_TIMEOUT", 60.0),
        credentials=[Credential(label=label, key=api_key, substrate=name)],
        builder=None,
    )
    spec.builder = _build_openai_substrate(spec)
    return spec


def _fake_spec(name: str) -> SubstrateSpec:
    spec = SubstrateSpec(
        name=name,
        model="fake",
        base_url=None,
        max_window=8_192,
        timeout_seconds=5.0,
        credentials=[Credential(label="fake-key", key="fake", substrate=name)],
        builder=lambda _cred: FakeFailingSubstrate(name=name),
    )
    return spec


# ---------------------------------------------------------------------------
# Dispatcher — pool + policy + adapters
# ---------------------------------------------------------------------------


# Argument keys whose *values* are high-risk to emit verbatim into the JSONL
# event log: file bodies, shell commands, raw payloads, and anything that
# commonly carries secrets. Their values are redacted before ``tool.invoked``
# is written so credentials in a write_file body or a bash command never land
# in a plaintext audit file.
_SENSITIVE_ARG_KEYS = frozenset(
    {
        "content",
        "command",
        "cmd",
        "code",
        "script",
        "body",
        "data",
        "text",
        "payload",
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "key",
        "authorization",
    }
)
_ARG_PREVIEW_CHARS = 40


def _redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``args`` safe to emit into the JSONL event log.

    Sensitive keys (file bodies, shell commands, secrets) are replaced with a
    length-only placeholder; other string values are length-capped so a large
    blob neither bloats nor leaks through the event file. Non-sensitive scalars
    pass through unchanged so the audit trail stays useful.
    """
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        if key.lower() in _SENSITIVE_ARG_KEYS:
            length = len(value) if isinstance(value, (str, bytes, list, dict)) else None
            redacted[key] = f"<redacted:{length} chars>" if length is not None else "<redacted>"
        elif isinstance(value, str) and len(value) > _ARG_PREVIEW_CHARS:
            redacted[key] = value[:_ARG_PREVIEW_CHARS] + f"…(+{len(value) - _ARG_PREVIEW_CHARS})"
        else:
            redacted[key] = value
    return redacted


def _classify_exception(exc: BaseException) -> Outcome:
    """Map an SDK exception class name to a Spec §11 outcome.

    Pessimistic by default — unknown classes become ``transient_exhausted``
    rather than ``success`` so a flaky substrate degrades visibly instead
    of silently looping.
    """
    name = type(exc).__name__
    if "Authentication" in name or "PermissionDenied" in name:
        return "auth"
    if "BadRequest" in name or "Unprocessable" in name or "NotFound" in name:
        return "hard_refusal"
    # Timeout / Connection / RateLimit / InternalServer / generic API → transient.
    return "transient_exhausted"


@dataclass
class DispatchResult:
    substrate: str
    label: str
    text: str
    in_tokens: int = 0
    out_tokens: int = 0
    finish_reason: str = "stop"
    elapsed_ms: float = 0.0


class _Attempt(NamedTuple):
    """One resolved failover attempt: the substrate/credential to call next."""

    substrate_name: str
    pool: CredentialPool
    cred: Credential
    substrate: Substrate


class Dispatcher:
    """Walks the failover chain on every turn.

    Mid-turn switching is disallowed (`FailoverPolicy.allow_mid_turn=False`),
    so a stream that errors after the first chunk surfaces directly — the
    REPL prints the error and the *next* turn fails over cleanly.
    """

    def __init__(
        self,
        specs: list[SubstrateSpec],
        sink: EventSink,
        *,
        max_tokens: int = 1024,
    ) -> None:
        if not specs:
            raise ValueError("Dispatcher requires at least one substrate spec")
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"Dispatcher requires unique substrate names; duplicates: {dupes}")
        self._specs: dict[str, SubstrateSpec] = {s.name: s for s in specs}
        self._pools: dict[str, CredentialPool] = {
            s.name: CredentialPool(s.name, s.credentials) for s in specs
        }
        self._adapters: dict[tuple[str, str], Substrate] = {}
        for spec in specs:
            for cred in spec.credentials:
                self._adapters[(spec.name, cred.label)] = spec.builder(cred)
        self.policy = FailoverPolicy(
            order=[s.name for s in specs],
            on_event=sink.callback,
            allow_mid_turn=False,
        )
        self._sink = sink
        self.max_tokens = max_tokens

    # --- accessors used by slash commands ------------------------------------

    @property
    def specs(self) -> dict[str, SubstrateSpec]:
        return self._specs

    @property
    def pools(self) -> dict[str, CredentialPool]:
        return self._pools

    def adapter(self, substrate: str, label: str) -> Substrate:
        return self._adapters[(substrate, label)]

    def set_active(self, substrate: str) -> None:
        """Operator-driven switch-back (§16) — bypasses chain advance.

        Delegates to :meth:`FailoverPolicy.force_active` rather than poking the
        policy's private ``_active`` directly, so the policy keeps ownership of
        its own invariants.
        """
        self.policy.force_active(substrate)

    # --- main dispatch -------------------------------------------------------

    def _next_live(self) -> _Attempt:
        """Advance the failover chain until a live credential is found.

        Raises ``NoLiveSubstrate`` (from ``next_substrate``) once every
        substrate's pool is exhausted; the REPL catches and reports it.
        """
        while True:
            active = self.policy.active()
            pool = self._pools[active]
            try:
                cred = pool.pick_live()
            except NoLiveCredential:
                # All creds for this substrate are benched — advance the chain.
                self.policy.next_substrate(after=active, reason="pool_exhausted")
                continue
            return _Attempt(
                substrate_name=active,
                pool=pool,
                cred=cred,
                substrate=self._adapters[(active, cred.label)],
            )

    def _record_failure(
        self, attempt: _Attempt, exc: Exception, started: float, **extra: object
    ) -> Outcome:
        """Classify a failed attempt, apply the cooldown ladder, emit the event.

        ``extra`` carries mode-specific fields (e.g. ``chunks`` for streaming).
        """
        outcome = _classify_exception(exc)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        attempt.pool.record_attempt(attempt.cred.label, outcome=outcome)
        self._sink.emit(
            "turn.attempt_failed",
            substrate=attempt.substrate_name,
            label=attempt.cred.label,
            outcome=outcome,
            error=type(exc).__name__,
            detail=str(exc)[:200],
            elapsed_ms=round(elapsed_ms, 1),
            **extra,
        )
        return outcome

    def complete(self, prompt: str) -> DispatchResult:
        """Run a non-streaming turn, failing over until success or chain exhausted."""
        while True:
            attempt = self._next_live()
            started = time.monotonic()
            try:
                result = attempt.substrate.complete(prompt, params={"max_tokens": self.max_tokens})
            except Exception as exc:
                if self._record_failure(attempt, exc, started) == "hard_refusal":
                    # Malformed input — another credential would fail identically.
                    raise
                continue
            elapsed_ms = (time.monotonic() - started) * 1000.0
            attempt.pool.record_attempt(attempt.cred.label, outcome="success")
            self._sink.emit(
                "turn.completed",
                substrate=attempt.substrate_name,
                label=attempt.cred.label,
                in_tokens=result.input_tokens,
                out_tokens=result.output_tokens,
                finish=result.finish_reason,
                elapsed_ms=round(elapsed_ms, 1),
            )
            return DispatchResult(
                substrate=attempt.substrate_name,
                label=attempt.cred.label,
                text=result.text,
                in_tokens=result.input_tokens,
                out_tokens=result.output_tokens,
                finish_reason=result.finish_reason,
                elapsed_ms=elapsed_ms,
            )

    def stream(self, prompt: str) -> DispatchResult:
        """Run a streaming turn, printing as chunks arrive."""
        while True:
            attempt = self._next_live()
            started = time.monotonic()
            chunks: list[str] = []
            try:
                for piece in attempt.substrate.stream(
                    prompt, params={"max_tokens": self.max_tokens}
                ):
                    chunks.append(piece)
                    sys.stdout.write(piece)
                    sys.stdout.flush()
            except Exception as exc:
                outcome = self._record_failure(attempt, exc, started, chunks=len(chunks))
                if chunks:
                    # Mid-turn failover would replay tokens at cost; spec §13
                    # forbids it unless allow_mid_turn is True. Surface and
                    # let the operator retry.
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    raise
                if outcome == "hard_refusal":
                    raise
                continue
            sys.stdout.write("\n")
            sys.stdout.flush()
            elapsed_ms = (time.monotonic() - started) * 1000.0
            attempt.pool.record_attempt(attempt.cred.label, outcome="success")
            self._sink.emit(
                "turn.completed",
                substrate=attempt.substrate_name,
                label=attempt.cred.label,
                chunks=len(chunks),
                elapsed_ms=round(elapsed_ms, 1),
            )
            return DispatchResult(
                substrate=attempt.substrate_name,
                label=attempt.cred.label,
                text="".join(chunks),
                elapsed_ms=elapsed_ms,
            )


# ---------------------------------------------------------------------------
# Transcript flattening (single-prompt substrate workaround)
# ---------------------------------------------------------------------------


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class Transcript:
    system: str = ""
    messages: list[Message] = field(default_factory=list)

    def reset(self) -> None:
        self.messages.clear()

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str) -> None:
        self.messages.append(Message(role="assistant", content=text))

    def render(self) -> str:
        """Flatten into a single prompt with explicit role headers.

        The substrate adapter wraps the whole string in one ``role=user``
        message; the role headers inside the text are what lets the model
        follow the multi-turn structure. This is a Stage-2 workaround
        spelled out so Stage 03 can replace it cleanly.
        """
        parts: list[str] = []
        if self.system:
            parts.append(f"[System]\n{self.system}\n")
        for msg in self.messages:
            label = "User" if msg.role == "user" else "Assistant"
            parts.append(f"[{label}]\n{msg.content}\n")
        parts.append("[Assistant]\n")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# REPL loop + slash commands
# ---------------------------------------------------------------------------


_HELP = """\
slash commands:
  /help                          this message
  /quit | /exit                  leave the repl
  /reset                         clear conversation (keeps system preamble)
  /system [text]                 set system preamble; no arg = show current
  /health                        probe every substrate
  /pool                          show credential pool state
  /sub                           show failover order + active substrate
  /fail <label> [auth|transient|hard]   manually bench / mark a credential
  /use <substrate>               operator switch-back (resets policy.active)
  /stream on|off                 toggle streaming output
  /tools                         list registered tools
  /tool <name> [json-args]       invoke one tool against the live registry
  /info                          full status dump
  /task list [status]            list background tasks
  /task output <id>              dump tail of a task's output_file
  /task stop <id>                stop a running task
  /cron list                     list cron jobs in the registry
  /cron show <name>              show one cron job's full details
  /cron toggle <name>            flip a cron job's enabled flag
  /plan list                     list exec-plans grouped by state
  /plan show <task_id>           show one plan's sections + ledger
"""


def _format_pool(pool: CredentialPool) -> str:
    if pool.is_empty():
        return f"  {pool.substrate}: <empty>"
    lines = [f"  {pool.substrate}:"]
    now = time.monotonic()
    # SLF001 isn't in our ruff select set so private access is fine here —
    # CredentialPool intentionally doesn't expose a public "all credentials"
    # iterator (the runner only ever wants ``live()``); the REPL is the one
    # caller that legitimately needs to display benched entries too.
    all_creds = list(pool._credentials)
    live = [c for c in all_creds if not c.is_benched()]
    benched = [c for c in all_creds if c.is_benched()]
    for cred in live + benched:
        if cred.is_benched():
            remaining = (cred.cooldown_until or now) - now
            state = (
                f"BENCHED rung={cred.rung} cooldown={remaining:.0f}s last_error={cred.last_error}"
            )
        else:
            state = f"live rung={cred.rung}"
        lines.append(f"    - {cred.label}: {state}")
    return "\n".join(lines)


@dataclass
class ReplState:
    """Mutable per-session REPL toggles. A typed struct, not a bare dict, so
    every access is type-checked and the field set is explicit."""

    stream: bool
    events_path: str
    registry: ToolRegistry | None = None
    cwd: Path = field(default_factory=Path.cwd)
    sink: EventSink | None = None
    task_manager: BackgroundTaskManager | None = None
    cron_registry_path: Path | None = None
    plans_root: Path | None = None


@dataclass
class _SlashContext:
    """Everything a slash-command handler may need; built once per invocation."""

    arg: str
    dispatcher: Dispatcher
    transcript: Transcript
    state: ReplState


def _cmd_help(ctx: _SlashContext) -> bool:
    print(_HELP, end="")
    return True


def _cmd_reset(ctx: _SlashContext) -> bool:
    ctx.transcript.reset()
    print("[reset]")
    return True


def _cmd_system(ctx: _SlashContext) -> bool:
    if not ctx.arg:
        print(f"system = {ctx.transcript.system!r}")
    else:
        ctx.transcript.system = ctx.arg
        print(f"[system set: {len(ctx.arg)} chars]")
    return True


def _cmd_health(ctx: _SlashContext) -> bool:
    for name, spec in ctx.dispatcher.specs.items():
        # Probe each credential's adapter; first one wins for display.
        cred = spec.credentials[0] if spec.credentials else None
        if cred is None:
            print(f"  {name}: <no credentials>")
            continue
        substrate = ctx.dispatcher.adapter(name, cred.label)
        report = substrate.health()
        ctx.dispatcher.policy.record_probe(name, healthy=(report.state == "ok"))
        print(f"  {name}: {report.state} ({report.detail}) {report.latency_ms:.0f}ms")
    return True


def _cmd_pool(ctx: _SlashContext) -> bool:
    for pool in ctx.dispatcher.pools.values():
        print(_format_pool(pool))
    return True


def _cmd_sub(ctx: _SlashContext) -> bool:
    active = ctx.dispatcher.policy.active()
    order = ctx.dispatcher.policy.order
    formatted = " > ".join(f"*{n}*" if n == active else n for n in order)
    print(f"  order: {formatted}")
    return True


def _cmd_fail(ctx: _SlashContext) -> bool:
    fail_parts = ctx.arg.split()
    if not fail_parts:
        print("usage: /fail <label> [auth|transient|hard]")
        return True
    label = fail_parts[0]
    kind = fail_parts[1] if len(fail_parts) > 1 else "auth"
    outcome_map: dict[str, Outcome] = {
        "auth": "auth",
        "transient": "transient_exhausted",
        "hard": "hard_refusal",
    }
    outcome = outcome_map.get(kind)
    if outcome is None:
        print(f"unknown kind {kind!r}; use auth|transient|hard")
        return True
    # Find the pool containing this label.
    for pool in ctx.dispatcher.pools.values():
        try:
            pool.get(label)
        except KeyError:
            continue
        pool.record_attempt(label, outcome=outcome)
        print(f"[recorded {outcome} on {pool.substrate}:{label}]")
        return True
    print(f"no credential labelled {label!r} in any pool")
    return True


def _cmd_use(ctx: _SlashContext) -> bool:
    if not ctx.arg or ctx.arg not in ctx.dispatcher.policy.order:
        print(f"unknown substrate {ctx.arg!r}; known: {ctx.dispatcher.policy.order}")
        return True
    # Operator-driven switch-back — bypass next_substrate's chain advance.
    ctx.dispatcher.set_active(ctx.arg)
    print(f"[active = {ctx.arg}]")
    return True


def _cmd_stream(ctx: _SlashContext) -> bool:
    arg = ctx.arg.lower()
    if arg in ("on", "true", "1"):
        ctx.state.stream = True
    elif arg in ("off", "false", "0"):
        ctx.state.stream = False
    else:
        print(f"stream = {ctx.state.stream}")
        return True
    print(f"[stream = {ctx.state.stream}]")
    return True


def _cmd_info(ctx: _SlashContext) -> bool:
    print(f"  active substrate: {ctx.dispatcher.policy.active()}")
    print(f"  failover order:   {ctx.dispatcher.policy.order}")
    print(f"  stream:           {ctx.state.stream}")
    print(
        f"  messages:         {len(ctx.transcript.messages)} "
        f"(system: {bool(ctx.transcript.system)})"
    )
    print(f"  events file:      {ctx.state.events_path}")
    print("  pools:")
    for pool in ctx.dispatcher.pools.values():
        print(_format_pool(pool))
    return True


def _cmd_tools(ctx: _SlashContext) -> bool:
    registry = ctx.state.registry
    if registry is None:
        print("tool registry not configured for this session")
        return True
    tools = registry.list_tools()
    if not tools:
        print("(no tools registered)")
        return True
    for tool in tools:
        decl = tool.declaration
        print(
            f"  {tool.name} [{decl.risk} tier={decl.tier_required} "
            f"timeout={decl.timeout_seconds:.1f}s]: {tool.description}"
        )
    return True


def _cmd_tool(ctx: _SlashContext) -> bool:
    registry = ctx.state.registry
    if registry is None:
        print("tool registry not configured for this session")
        return True
    if not ctx.arg.strip():
        print('usage: /tool <name> [json-args]   (e.g. /tool read_file {"path": "x"})')
        return True
    name, _, raw_args = ctx.arg.strip().partition(" ")
    tool = registry.get(name)
    if tool is None:
        known = ", ".join(t.name for t in registry.list_tools()) or "<none>"
        print(f"unknown tool {name!r}; known: {known}")
        return True
    raw_args = raw_args.strip() or "{}"
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        print(f"[error] could not parse json args: {exc}")
        return True
    if not isinstance(args, dict):
        print(f"[error] json args must be an object, got {type(args).__name__}")
        return True

    tool_ctx = ToolExecutionContext(
        working_dir=ctx.state.cwd,
        session_id="repl",
    )
    sink = ctx.state.sink
    if sink is not None:
        # Redact high-risk argument values (file bodies, shell commands,
        # secrets) before they hit the plaintext JSONL audit file.
        sink.emit("tool.invoked", name=name, args=_redact_args(args))
    timeout = tool.declaration.timeout_seconds
    started = time.monotonic()
    try:
        result = asyncio.run(asyncio.wait_for(tool.execute(args, tool_ctx), timeout=timeout))
    except TimeoutError:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        print(f"[error] tool {name!r} timed out after {timeout:.1f}s")
        if sink is not None:
            sink.emit(
                "tool.failed",
                name=name,
                error="TimeoutError",
                detail=f"exceeded declared timeout of {timeout:.1f}s",
                elapsed_ms=round(elapsed_ms, 1),
            )
        return True
    except Exception as exc:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        print(f"[error] {type(exc).__name__}: {exc}")
        if sink is not None:
            sink.emit(
                "tool.failed",
                name=name,
                error=type(exc).__name__,
                detail=str(exc)[:200],
                elapsed_ms=round(elapsed_ms, 1),
            )
        return True

    elapsed_ms = (time.monotonic() - started) * 1000.0
    label = "ERROR" if result.is_error else "ok"
    print(f"[tool={name} {label}] elapsed={elapsed_ms:.0f}ms")
    if result.content:
        print(result.content)
    md = result.metadata or {}
    for key in ("root_cause", "safe_retry", "stop_condition"):
        if key in md:
            print(f"  {key}: {md[key]}")
    if sink is not None:
        if result.is_error:
            sink.emit(
                "tool.failed",
                name=name,
                is_error=True,
                elapsed_ms=round(elapsed_ms, 1),
                metadata=md,
            )
        else:
            sink.emit(
                "tool.completed",
                name=name,
                is_error=False,
                elapsed_ms=round(elapsed_ms, 1),
                metadata=md,
            )
    return True


# --- slice-3: /task /cron /plan -----------------------------------------


def _task_usage() -> None:
    print("usage: /task list [status] | /task output <id> | /task stop <id>")


def _cmd_task(ctx: _SlashContext) -> bool:
    mgr = ctx.state.task_manager
    if mgr is None:
        print("task manager not configured for this session")
        return True
    parts = ctx.arg.split()
    if not parts:
        _task_usage()
        return True
    sub, rest = parts[0], parts[1:]
    if sub == "list":
        status = rest[0] if rest else None
        tasks = mgr.list_tasks(status=status)  # type: ignore[arg-type]
        if not tasks:
            print("(no tasks)")
            return True
        for t in tasks:
            print(f"  {t.id} [{t.status}] {t.description}")
        return True
    if sub == "output":
        if not rest:
            _task_usage()
            return True
        task_id = rest[0]
        if mgr.get_task(task_id) is None:
            print(f"unknown task {task_id!r}")
            return True
        print(mgr.read_task_output(task_id))
        return True
    if sub == "stop":
        if not rest:
            _task_usage()
            return True
        task_id = rest[0]
        if mgr.get_task(task_id) is None:
            print(f"unknown task {task_id!r}")
            return True
        asyncio.run(mgr.stop_task(task_id))
        print(f"[stopped {task_id}]")
        return True
    _task_usage()
    return True


def _cron_usage() -> None:
    print("usage: /cron list | /cron show <name> | /cron toggle <name>")


def _cmd_cron(ctx: _SlashContext) -> bool:
    registry = ctx.state.cron_registry_path
    if registry is None:
        print("cron registry not configured for this session")
        return True
    parts = ctx.arg.split()
    if not parts:
        _cron_usage()
        return True
    sub, rest = parts[0], parts[1:]
    if sub == "list":
        jobs = load_cron_jobs(registry)
        if not jobs:
            print("(no cron jobs)")
            return True
        for j in jobs:
            state = "enabled" if j.enabled else "disabled"
            tz = f" tz={j.timezone}" if j.timezone else ""
            nxt = f" next={j.next_run.isoformat()}" if j.next_run else ""
            print(f"  {j.name}  {j.schedule!r}{tz}  [{state}]{nxt}")
        return True
    if sub == "show":
        if not rest:
            _cron_usage()
            return True
        name = rest[0]
        job = get_cron_job(registry, name)
        if job is None:
            print(f"unknown cron job {name!r}")
            return True
        print(f"  name:           {job.name}")
        print(f"  schedule:       {job.schedule}")
        print(f"  timezone:       {job.timezone or '<none>'}")
        print(f"  enabled:        {job.enabled}")
        print(f"  tier_required:  {job.tier_required or '<none>'}")
        print(f"  description:    {job.description or '<none>'}")
        print(f"  next_run:       {job.next_run.isoformat() if job.next_run else '<unset>'}")
        print(f"  last_run:       {job.last_run.isoformat() if job.last_run else '<never>'}")
        print(f"  last_status:    {job.last_status or '<none>'}")
        return True
    if sub == "toggle":
        if not rest:
            _cron_usage()
            return True
        name = rest[0]
        job = get_cron_job(registry, name)
        if job is None:
            print(f"unknown cron job {name!r}")
            return True
        new_state = not job.enabled
        set_job_enabled(registry, name, enabled=new_state)
        print(f"[{name} now {'enabled' if new_state else 'disabled'}]")
        return True
    _cron_usage()
    return True


def _plan_usage() -> None:
    print("usage: /plan list | /plan show <task_id>")


def _cmd_plan(ctx: _SlashContext) -> bool:
    root = ctx.state.plans_root
    if root is None:
        print("plan store not configured for this session")
        return True
    parts = ctx.arg.split()
    if not parts:
        _plan_usage()
        return True
    sub, rest = parts[0], parts[1:]
    if sub == "list":
        any_found = False
        for state in PLAN_STATES:
            d = plan_dir(root, state=state)
            if not d.is_dir():
                continue
            tasks = sorted(p.stem for p in d.glob("*.json"))
            if not tasks:
                continue
            any_found = True
            print(f"  [{state}]")
            for task_id in tasks:
                print(f"    {task_id}")
        if not any_found:
            print("(no plans)")
        return True
    if sub == "show":
        if not rest:
            _plan_usage()
            return True
        task_id = rest[0]
        for state in PLAN_STATES:
            d = plan_dir(root, state=state)
            if not (d / f"{task_id}.json").exists():
                continue
            plan = read_plan(d, task_id=task_id)
            print(f"# {plan.task_id}  [{plan.ledger.state}]")
            for section in plan.sections:
                body = plan.sections[section].strip()
                first = body.splitlines()[0] if body else ""
                print(f"  {section}: {first}")
            print("  entries:")
            for e in plan.ledger.entries:
                print(f"    - {e.id} [{e.status}] {e.description}")
            return True
        print(f"unknown plan {task_id!r}")
        return True
    _plan_usage()
    return True


# Command → handler. ``/quit`` and ``/exit`` are handled inline in ``_slash``
# because they alone return False (leave the loop); every handler here returns
# True (keep looping).
_SLASH_COMMANDS: dict[str, Callable[[_SlashContext], bool]] = {
    "/help": _cmd_help,
    "/reset": _cmd_reset,
    "/system": _cmd_system,
    "/health": _cmd_health,
    "/pool": _cmd_pool,
    "/sub": _cmd_sub,
    "/fail": _cmd_fail,
    "/use": _cmd_use,
    "/stream": _cmd_stream,
    "/tools": _cmd_tools,
    "/tool": _cmd_tool,
    "/info": _cmd_info,
    "/task": _cmd_task,
    "/cron": _cmd_cron,
    "/plan": _cmd_plan,
}


def _slash(line: str, *, dispatcher: Dispatcher, transcript: Transcript, state: ReplState) -> bool:
    """Dispatch one slash command. Returns True to keep looping, False to quit."""
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit"):
        return False

    handler = _SLASH_COMMANDS.get(cmd)
    if handler is None:
        print(f"unknown command {cmd!r}; /help for list")
        return True
    return handler(
        _SlashContext(arg=arg, dispatcher=dispatcher, transcript=transcript, state=state)
    )


def run_chat(
    specs: Iterable[SubstrateSpec],
    *,
    events_path: Path,
    max_tokens: int = 1024,
    initial_stream: bool = True,
) -> int:
    spec_list = list(specs)  # materialize once — consumed for both event + dispatcher
    sink = EventSink(events_path)
    sink.emit("repl.started", substrates=[s.name for s in spec_list])
    dispatcher = Dispatcher(spec_list, sink, max_tokens=max_tokens)
    transcript = Transcript()
    registry = default_registry()
    state = ReplState(
        stream=initial_stream,
        events_path=str(events_path),
        registry=registry,
        cwd=Path.cwd(),
        sink=sink,
    )

    print(f"dream repl — events -> {events_path}")
    print(f"substrates: {dispatcher.policy.order} (active={dispatcher.policy.active()})")
    print(f"tools: {len(registry)} registered (/tools to list)")
    print("type /help for commands, /quit to exit")

    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        if line.startswith("/"):
            if not _slash(line, dispatcher=dispatcher, transcript=transcript, state=state):
                break
            continue

        transcript.add_user(line)
        prompt = transcript.render()
        try:
            if state.stream:
                result = dispatcher.stream(prompt)
            else:
                result = dispatcher.complete(prompt)
                print(result.text)
        except NoLiveSubstrate as exc:
            print(f"[failover chain exhausted] {exc}")
            # Roll back the user message — the turn never completed.
            transcript.messages.pop()
            continue
        except Exception as exc:
            print(f"[turn failed] {type(exc).__name__}: {exc}")
            transcript.messages.pop()
            continue

        transcript.add_assistant(result.text)
        footer_bits = [
            f"substrate={result.substrate}",
            f"label={result.label}",
            f"elapsed={result.elapsed_ms:.0f}ms",
        ]
        if result.in_tokens or result.out_tokens:
            footer_bits.append(f"in={result.in_tokens}")
            footer_bits.append(f"out={result.out_tokens}")
        if result.finish_reason and result.finish_reason != "stop":
            footer_bits.append(f"finish={result.finish_reason}")
        print(f"  [{' '.join(footer_bits)}]")

    sink.emit("repl.stopped")
    return 0


# ---------------------------------------------------------------------------
# CLI entry-point glue (used by __main__)
# ---------------------------------------------------------------------------


def build_specs(
    *,
    fake_primary: bool,
    fake_fallback: bool,
) -> list[SubstrateSpec]:
    """Compose the substrate chain from env + flags."""
    specs: list[SubstrateSpec] = []
    if fake_primary:
        specs.append(_fake_spec("fake-primary"))
    real = _spec_from_env()
    if real is not None:
        specs.append(real)
    if fake_fallback:
        specs.append(_fake_spec("fake-fallback"))
    if not specs:
        # No real env and no fake flags — still let the user explore the
        # REPL with a single fake substrate (so they can see /pool, /sub).
        specs.append(_fake_spec("fake-only"))
    return specs


__all__ = [
    "DispatchResult",
    "Dispatcher",
    "Message",
    "ReplState",
    "SubstrateSpec",
    "Transcript",
    "build_specs",
    "run_chat",
]
