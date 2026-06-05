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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
        max_window=int(os.environ.get("DREAM_SMOKE_MAX_WINDOW", "128000")),
        timeout_seconds=float(os.environ.get("DREAM_SMOKE_TIMEOUT", "60")),
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

        Distinct from :meth:`FailoverPolicy.next_substrate`: that walks the
        chain on pool exhaustion; this is the operator saying "go back to
        the primary, I cleared its issue". Emits no event.
        """
        if substrate not in self.policy.order:
            raise ValueError(f"unknown substrate {substrate!r}")
        self.policy._active = substrate

    # --- main dispatch -------------------------------------------------------

    def complete(self, prompt: str) -> DispatchResult:
        """Run a non-streaming turn, failing over until success or chain exhausted."""
        while True:
            active = self.policy.active()
            pool = self._pools[active]
            try:
                cred = pool.pick_live()
            except NoLiveCredential:
                # All creds for this substrate are benched — advance the chain.
                # next_substrate raises NoLiveSubstrate when exhausted; let it
                # propagate to the REPL which catches and reports.
                self.policy.next_substrate(after=active, reason="pool_exhausted")
                continue
            substrate = self._adapters[(active, cred.label)]
            started = time.monotonic()
            try:
                result = substrate.complete(prompt, params={"max_tokens": self.max_tokens})
            except Exception as exc:
                outcome = _classify_exception(exc)
                elapsed_ms = (time.monotonic() - started) * 1000.0
                pool.record_attempt(cred.label, outcome=outcome)
                self._sink.emit(
                    "turn.attempt_failed",
                    substrate=active,
                    label=cred.label,
                    outcome=outcome,
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                    elapsed_ms=round(elapsed_ms, 1),
                )
                if outcome == "hard_refusal":
                    # Malformed input — retrying on another credential will
                    # produce the same error. Surface to the operator.
                    raise
                continue
            elapsed_ms = (time.monotonic() - started) * 1000.0
            pool.record_attempt(cred.label, outcome="success")
            self._sink.emit(
                "turn.completed",
                substrate=active,
                label=cred.label,
                in_tokens=result.input_tokens,
                out_tokens=result.output_tokens,
                finish=result.finish_reason,
                elapsed_ms=round(elapsed_ms, 1),
            )
            return DispatchResult(
                substrate=active,
                label=cred.label,
                text=result.text,
                in_tokens=result.input_tokens,
                out_tokens=result.output_tokens,
                finish_reason=result.finish_reason,
                elapsed_ms=elapsed_ms,
            )

    def stream(self, prompt: str) -> DispatchResult:
        """Run a streaming turn, printing as chunks arrive."""
        while True:
            active = self.policy.active()
            pool = self._pools[active]
            try:
                cred = pool.pick_live()
            except NoLiveCredential:
                self.policy.next_substrate(after=active, reason="pool_exhausted")
                continue
            substrate = self._adapters[(active, cred.label)]
            started = time.monotonic()
            chunks: list[str] = []
            try:
                for piece in substrate.stream(prompt, params={"max_tokens": self.max_tokens}):
                    chunks.append(piece)
                    sys.stdout.write(piece)
                    sys.stdout.flush()
            except Exception as exc:
                outcome = _classify_exception(exc)
                elapsed_ms = (time.monotonic() - started) * 1000.0
                pool.record_attempt(cred.label, outcome=outcome)
                self._sink.emit(
                    "turn.attempt_failed",
                    substrate=active,
                    label=cred.label,
                    outcome=outcome,
                    error=type(exc).__name__,
                    chunks=len(chunks),
                    detail=str(exc)[:200],
                    elapsed_ms=round(elapsed_ms, 1),
                )
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
            pool.record_attempt(cred.label, outcome="success")
            self._sink.emit(
                "turn.completed",
                substrate=active,
                label=cred.label,
                chunks=len(chunks),
                elapsed_ms=round(elapsed_ms, 1),
            )
            return DispatchResult(
                substrate=active,
                label=cred.label,
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


def _slash(line: str, *, dispatcher: Dispatcher, transcript: Transcript, state: dict[str, Any]) -> bool:
    """Dispatch one slash command. Returns True to keep looping, False to quit."""
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit"):
        return False

    if cmd == "/help":
        print(_HELP, end="")
        return True

    if cmd == "/reset":
        transcript.reset()
        print("[reset]")
        return True

    if cmd == "/system":
        if not arg:
            print(f"system = {transcript.system!r}")
        else:
            transcript.system = arg
            print(f"[system set: {len(arg)} chars]")
        return True

    if cmd == "/health":
        for name, spec in dispatcher.specs.items():
            # Probe each credential's adapter; first one wins for display.
            cred = spec.credentials[0] if spec.credentials else None
            if cred is None:
                print(f"  {name}: <no credentials>")
                continue
            substrate = dispatcher.adapter(name, cred.label)
            report = substrate.health()
            dispatcher.policy.record_probe(name, healthy=(report.state == "ok"))
            print(f"  {name}: {report.state} ({report.detail}) {report.latency_ms:.0f}ms")
        return True

    if cmd == "/pool":
        for pool in dispatcher.pools.values():
            print(_format_pool(pool))
        return True

    if cmd == "/sub":
        active = dispatcher.policy.active()
        order = dispatcher.policy.order
        formatted = " > ".join(f"*{n}*" if n == active else n for n in order)
        print(f"  order: {formatted}")
        return True

    if cmd == "/fail":
        fail_parts = arg.split()
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
        for pool in dispatcher.pools.values():
            try:
                pool.get(label)
            except KeyError:
                continue
            pool.record_attempt(label, outcome=outcome)
            print(f"[recorded {outcome} on {pool.substrate}:{label}]")
            return True
        print(f"no credential labelled {label!r} in any pool")
        return True

    if cmd == "/use":
        if not arg or arg not in dispatcher.policy.order:
            print(f"unknown substrate {arg!r}; known: {dispatcher.policy.order}")
            return True
        # Operator-driven switch-back — bypass next_substrate's chain advance.
        dispatcher.set_active(arg)
        print(f"[active = {arg}]")
        return True

    if cmd == "/stream":
        if arg.lower() in ("on", "true", "1"):
            state["stream"] = True
        elif arg.lower() in ("off", "false", "0"):
            state["stream"] = False
        else:
            print(f"stream = {state['stream']}")
            return True
        print(f"[stream = {state['stream']}]")
        return True

    if cmd == "/info":
        print(f"  active substrate: {dispatcher.policy.active()}")
        print(f"  failover order:   {dispatcher.policy.order}")
        print(f"  stream:           {state['stream']}")
        print(f"  messages:         {len(transcript.messages)} (system: {bool(transcript.system)})")
        print(f"  events file:      {state['events_path']}")
        print("  pools:")
        for pool in dispatcher.pools.values():
            print(_format_pool(pool))
        return True

    print(f"unknown command {cmd!r}; /help for list")
    return True


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
    state: dict[str, Any] = {"stream": initial_stream, "events_path": str(events_path)}

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
            if state["stream"]:
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
    "SubstrateSpec",
    "Transcript",
    "build_specs",
    "run_chat",
]
