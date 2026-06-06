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
            raise ValueError(
                f"Dispatcher requires unique substrate names; duplicates: {dupes}"
            )
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
                result = attempt.substrate.complete(
                    prompt, params={"max_tokens": self.max_tokens}
                )
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
  /info                          full status dump
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
            state = f"BENCHED rung={cred.rung} cooldown={remaining:.0f}s last_error={cred.last_error}"
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
    "/info": _cmd_info,
}


def _slash(
    line: str, *, dispatcher: Dispatcher, transcript: Transcript, state: ReplState
) -> bool:
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
    state = ReplState(stream=initial_stream, events_path=str(events_path))

    print(f"dream repl — events -> {events_path}")
    print(f"substrates: {dispatcher.policy.order} (active={dispatcher.policy.active()})")
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
