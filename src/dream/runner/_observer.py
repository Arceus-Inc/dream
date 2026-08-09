"""Run-time observer for ``Harness.run_task`` and ``run_role``.

The runner is silent by default: every meaningful boundary is captured
into the returned ``RunTaskResult.events`` tuple but nothing is written
anywhere. For interactive operator use that's the wrong default — when
you type ``await harness.run_task(intent="…")`` you want to *see* the
planner draft a spec, every tool the generator calls, and the evaluator's
verdict as they happen.

This module ships two things:

1. :class:`RunTaskObserver` — a single-method ``Protocol`` consumed by
   :func:`dream.runner.run_task` and :func:`dream.runner.run_role`.
   The contract is minimal on purpose: callers receive plain dicts with
   a stable ``"kind"`` discriminator + free-form payload. New event
   kinds may be added without breaking existing observers.

2. :class:`StdioObserver` — the default impl. Writes a single
   human-readable line per event to a configurable text stream
   (defaults to ``sys.stdout``). Designed so the operator running
   ``await harness.run_task(intent="…")`` sees a live walkthrough
   without any further configuration.

Event kinds emitted today (additions are non-breaking):

* ``task.started``     — task_id, intent
* ``task.completed``   — task_id, sprint_count
* ``planner.started``  — task_id, intent
* ``planner.completed``— task_id, spec_path, ledger_path, step_count
* ``sprint.started``   — sprint_number, step_id, step_description
* ``sprint.completed`` — sprint_number, step_id, outcome|None
* ``contract.written`` — sprint_number, path
* ``generator.started``— sprint_number, step_id, has_contract
* ``generator.completed`` — sprint_number, step_id
* ``evaluator.started``— sprint_number, step_id
* ``evaluator.completed`` — sprint_number, outcome, score, notes
* ``role.session.opened`` — role, session_id
* ``role.session.recovered`` — role, session_id, requested_session_id, reason, action,
  snapshot_preserved
* ``role.session.closed`` — role, session_id, cost_usd
* ``role.text``        — role, text   (streamed deltas during run_role)
* ``role.tool.start``  — role, tool, input
* ``role.tool.result`` — role, tool, is_error, content_preview
* ``role.error``       — role, message
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Protocol, TextIO, runtime_checkable

# The ANSI constants and the TTY gate live in one shared module
# (:mod:`dream.runner._ansi`); aliased here under the module-private
# ``_NAME`` spelling every handler already uses.
from dream.runner._ansi import (
    BLUE as _BLUE,
)
from dream.runner._ansi import (
    BOLD as _BOLD,
)
from dream.runner._ansi import (
    CYAN as _CYAN,
)
from dream.runner._ansi import (
    DIM as _DIM,
)
from dream.runner._ansi import (
    GREEN as _GREEN,
)
from dream.runner._ansi import (
    RED as _RED,
)
from dream.runner._ansi import (
    RESET as _RESET,
)
from dream.runner._ansi import (
    YELLOW as _YELLOW,
)
from dream.runner._ansi import use_colour as _shared_use_colour

__all__ = [
    "RunTaskObserver",
    "StdioObserver",
    "_CapturingObserver",
]


@runtime_checkable
class RunTaskObserver(Protocol):
    """Called by the runner / role-session for every progress boundary.

    Implementations MUST be cheap and non-blocking — the runner calls
    ``on_event`` synchronously inside the hot path. Anything heavyweight
    (network log shipping, indexing, etc.) should enqueue and return.

    The event dict always has a ``"kind"`` string. Other keys are
    kind-specific; consumers should ``.get(...)`` with a default rather
    than indexing, so they survive future field additions.
    """

    def on_event(self, event: dict[str, Any]) -> None: ...


# --- visual styling (TTY-gated; StringIO and pipes get plain text) ----------

_ROLE_COLOUR: dict[str, str] = {
    "planner": _CYAN,
    "generator": _GREEN,
    "evaluator": _YELLOW,
}

_OUTCOME_COLOUR: dict[str, str] = {
    "pass": _GREEN,
    "needs-changes": _YELLOW,
    "fail": _RED,
}


def _use_colour(stream: TextIO) -> bool:
    """Only emit ANSI when the stream is a real terminal.

    StringIO/pipes/redirected files return False so test snapshots and
    machine-consumed logs stay plain text. ``NO_COLOR`` forces plain text
    even on a TTY.
    """
    return _shared_use_colour(stream, respect_no_color=True)


def _truncate(text: str, *, limit: int = 160) -> str:
    """One-line preview of an arbitrary string."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _short_path(p: object) -> str:
    """Render a path as just its basename when it's an absolute Windows/POSIX path.

    Keeps short relative paths intact (`hello.py` stays `hello.py`); collapses
    `C:\\Users\\…\\Temp\\dream-demo-xxx\\hello.py` to `hello.py` so the log
    is scannable.
    """
    s = str(p)
    if len(s) <= 60:
        return s
    for sep in ("\\", "/"):
        idx = s.rfind(sep)
        if idx != -1:
            tail = s[idx + 1 :]
            if tail:
                return f"…/{tail}"
    return _truncate(s, limit=60)


def _format_tool_input(value: Any) -> str:
    """Render tool input dicts compactly: drop noisy keys; shorten paths.

    `{'path': 'hello.py', 'offset': 0, 'limit': 200}` → `path=hello.py`
    `{'args': ['status', '--short']}` → `args=[status, --short]`
    `{'command': 'python -m pytest', 'cwd': 'C:/.../foo', 'timeout': 120}` →
        `command="python -m pytest" cwd=…/foo`
    """
    if not isinstance(value, dict):
        return _truncate(repr(value), limit=120)

    skip = {"offset", "limit", "timeout_seconds", "timeout", "max_bytes"}
    parts: list[str] = []
    for key, raw in value.items():
        if key in skip:
            continue
        if key in {"path", "cwd", "file"}:
            parts.append(f"{key}={_short_path(raw)}")
        elif key == "content" and isinstance(raw, str):
            parts.append(f"content={_truncate(raw, limit=40)!r}")
        elif key in {"command", "cmd"} and isinstance(raw, str):
            parts.append(f"{key}={_truncate(raw, limit=80)!r}")
        elif key == "args" and isinstance(raw, list):
            inner = ", ".join(str(x) for x in raw[:6])
            parts.append(f"args=[{_truncate(inner, limit=60)}]")
        else:
            parts.append(f"{key}={_truncate(repr(raw), limit=40)}")
        if sum(len(p) for p in parts) > 100:
            break
    return " ".join(parts) if parts else "{}"


@dataclass
class StdioObserver:
    """Default observer: one styled line per event on ``sys.stdout``.

    Output is plain ASCII unless ``stream`` is a TTY, in which case lines
    are ANSI-coloured by role and indented under their parent sprint /
    task scope. Set the ``NO_COLOR`` env var to force plain text on a
    TTY. ``role_text_buffering`` (default ``True``) accumulates a role's
    streamed text deltas and prints them as one ``reply:`` block on
    session close — readable for humans. Set it to ``False`` for raw
    delta-per-line streaming.
    """

    stream: TextIO = field(default_factory=lambda: sys.stdout)
    role_text_buffering: bool = True

    _text_buffers: dict[str, list[str]] = field(default_factory=dict)
    _colour_cache: bool | None = field(default=None, init=False, repr=False)

    @property
    def _colour(self) -> bool:
        if self._colour_cache is None:
            object.__setattr__(self, "_colour_cache", _use_colour(self.stream))
        return bool(self._colour_cache)

    def _c(self, code: str, text: str) -> str:
        if not self._colour or not code:
            return text
        return f"{code}{text}{_RESET}"

    def on_event(self, event: dict[str, Any]) -> None:
        # ``event`` always carries a ``"kind"`` str discriminator; the
        # remaining keys are fixed per kind (see the module docstring for the
        # full table). Examples:
        #   {"kind": "sprint.started", "sprint_number": 1,
        #    "step_id": "s1", "step_description": "…"}
        #   {"kind": "role.tool.start", "role": "generator",
        #    "tool": "read_file", "input": {"path": "x"}}
        #   {"kind": "evaluator.completed", "outcome": "pass",
        #    "score": 0.9, "notes": "…", "sprint_number": 1}
        kind = event.get("kind", "?")
        handler = _HANDLERS.get(kind, _format_default)
        line = handler(event, self)
        if line is None:
            return
        self.stream.write(line)
        if not line.endswith("\n"):
            self.stream.write("\n")
        self.stream.flush()


@dataclass
class _CapturingObserver:
    """Test helper: records every event into ``events`` in order."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def on_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


# --- handlers ---------------------------------------------------------------
#
# Test-pinned prefixes (`[task] start`, `[planner] done`, `[sprint 1] start`,
# `[generator] tool→ name`, etc.) are preserved verbatim so external tooling
# can grep them. ANSI codes wrap the whole line so substrings still match
# under `assert "<substring>" in out`.
#
# Indentation hierarchy:
#   * task / planner / sprint  → flush left
#   * role inside a phase      → 2 spaces
#   * tool inside a role       → 4 spaces


_INDENT_ROLE = "  "
_INDENT_TOOL = "    "


def _outcome_glyph(outcome: str) -> str:
    """Map an evaluation outcome to its display glyph (✓ pass / ✗ fail / ▲ else)."""
    if outcome == "pass":
        return "✓"
    if outcome == "fail":
        return "✗"
    return "▲"


def _format_default(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[?] {event.get('kind', 'unknown')} {event}"


def _on_task_started(event: dict[str, Any], obs: StdioObserver) -> str:
    line = (
        f"▶ [task] start task_id={event.get('task_id')!r} "
        f"intent={_truncate(str(event.get('intent', '')))!r}"
    )
    return obs._c(_BOLD + _BLUE, line)


def _on_task_completed(event: dict[str, Any], obs: StdioObserver) -> str:
    line = (
        f"✓ [task] done task_id={event.get('task_id')!r} "
        f"sprints={event.get('sprint_count')}"
    )
    return obs._c(_BOLD + _BLUE, line)


def _on_planner_started(event: dict[str, Any], obs: StdioObserver) -> str:
    return obs._c(_CYAN, f"◇ [planner] drafting spec for {event.get('task_id')!r}")


def _on_planner_completed(event: dict[str, Any], obs: StdioObserver) -> str:
    line = (
        f"✓ [planner] done — wrote {_short_path(event.get('spec_path'))} + "
        f"{_short_path(event.get('ledger_path'))} ({event.get('step_count')} steps)"
    )
    return obs._c(_CYAN, line)


def _on_sprint_started(event: dict[str, Any], obs: StdioObserver) -> str:
    line = (
        f"\n▶ [sprint {event.get('sprint_number')}] start "
        f"step={event.get('step_id')!r} — "
        f"{_truncate(str(event.get('step_description', '')))}"
    )
    return obs._c(_BOLD + _BLUE, line)


def _on_sprint_completed(event: dict[str, Any], obs: StdioObserver) -> str:
    outcome = event.get("outcome")
    if outcome:
        glyph = _outcome_glyph(outcome)
        colour = _OUTCOME_COLOUR.get(outcome, "")
        suffix = obs._c(colour, f"outcome={outcome}")
    else:
        glyph = "·"
        suffix = "outcome=(evaluator disabled)"
    line = (
        f"{glyph} [sprint {event.get('sprint_number')}] done "
        f"step={event.get('step_id')!r} {suffix}"
    )
    return obs._c(_BOLD, line)


def _on_contract_written(event: dict[str, Any], obs: StdioObserver) -> str:
    line = (
        f"◇ [sprint {event.get('sprint_number')}] contract written → "
        f"{_short_path(event.get('path'))}"
    )
    return obs._c(_CYAN, line)


def _on_generator_started(event: dict[str, Any], obs: StdioObserver) -> str:
    flag = "with contract" if event.get("has_contract") else "no contract (evaluator off)"
    line = (
        f"▶ [sprint {event.get('sprint_number')}] generator start "
        f"step={event.get('step_id')!r} ({flag})"
    )
    return obs._c(_GREEN, line)


def _on_generator_completed(event: dict[str, Any], obs: StdioObserver) -> str:
    line = (
        f"✓ [sprint {event.get('sprint_number')}] generator done "
        f"step={event.get('step_id')!r}"
    )
    return obs._c(_GREEN, line)


def _on_evaluator_started(event: dict[str, Any], obs: StdioObserver) -> str:
    line = (
        f"▶ [sprint {event.get('sprint_number')}] evaluator start "
        f"step={event.get('step_id')!r}"
    )
    return obs._c(_YELLOW, line)


def _on_evaluator_completed(event: dict[str, Any], obs: StdioObserver) -> str:
    outcome = str(event.get("outcome") or "")
    glyph = _outcome_glyph(outcome)
    colour = _OUTCOME_COLOUR.get(outcome, _YELLOW)
    notes = event.get("notes") or ""
    notes_part = f" notes={_truncate(notes)!r}" if notes else ""
    line = (
        f"{glyph} [sprint {event.get('sprint_number')}] evaluator done "
        f"outcome={outcome!r} score={event.get('score')}{notes_part}"
    )
    return obs._c(colour, line)


def _on_role_session_opened(event: dict[str, Any], obs: StdioObserver) -> str:
    role = str(event.get("role", "?"))
    colour = _ROLE_COLOUR.get(role, "")
    line = f"{_INDENT_ROLE}╭ [{role}] session open id={event.get('session_id')!r}"
    return obs._c(_DIM + colour, line)


def _on_role_session_recovered(event: dict[str, object], obs: StdioObserver) -> str:
    role = str(event.get("role", "?"))
    colour = _ROLE_COLOUR.get(role, "")
    line = (
        f"{_INDENT_ROLE}[{role}] session recovered "
        f"id={event.get('session_id')!r} requested={event.get('requested_session_id')!r} "
        f"reason={event.get('reason')!r} "
        f"action={event.get('action')!r}"
    )
    return obs._c(_YELLOW + colour, line)


def _on_role_session_closed(event: dict[str, Any], obs: StdioObserver) -> str:
    role = str(event.get("role", "?"))
    colour = _ROLE_COLOUR.get(role, "")
    cost = event.get("cost_usd")
    cost_str = f"${cost:.4f}" if isinstance(cost, (int, float)) and cost > 0 else "$0"
    reply_block = ""
    if obs.role_text_buffering:
        chunks = obs._text_buffers.pop(role, None)
        if chunks:
            joined = "".join(chunks).strip()
            if joined:
                reply = _truncate(joined, limit=400)
                reply_block = (
                    f"\n{_INDENT_ROLE}{obs._c(colour, f'[{role}] reply:')} "
                    f"{obs._c(_DIM, reply)}"
                )
    line = (
        f"{_INDENT_ROLE}╰ [{role}] session close "
        f"id={event.get('session_id')!r} cost_usd={cost} ({cost_str})"
    )
    return obs._c(_DIM + colour, line) + reply_block


def _on_role_text(event: dict[str, Any], obs: StdioObserver) -> str | None:
    role = str(event.get("role", "?"))
    text = str(event.get("text", ""))
    if obs.role_text_buffering:
        obs._text_buffers.setdefault(role, []).append(text)
        return None
    colour = _ROLE_COLOUR.get(role, "")
    return obs._c(colour, f"{_INDENT_ROLE}[{role}] {text}")


def _on_role_tool_start(event: dict[str, Any], obs: StdioObserver) -> str:
    role = str(event.get("role", "?"))
    tool = event.get("tool", "?")
    input_preview = _format_tool_input(event.get("input", {}))
    colour = _ROLE_COLOUR.get(role, "")
    line = f"{_INDENT_TOOL}[{role}] tool→ {tool} {input_preview}"
    return obs._c(colour, line)


def _on_role_tool_result(event: dict[str, Any], obs: StdioObserver) -> str:
    role = str(event.get("role", "?"))
    tool = event.get("tool")
    is_error = bool(event.get("is_error"))
    flag = "error" if is_error else "ok"
    preview = _truncate(str(event.get("content_preview", "")))
    role_colour = _ROLE_COLOUR.get(role, "")
    flag_colour = _RED if is_error else _GREEN
    head = obs._c(role_colour, f"{_INDENT_TOOL}[{role}] tool← {tool}")
    flag_styled = obs._c(flag_colour, f"[{flag}]")
    body = obs._c(_DIM, preview) if obs._colour else preview
    return f"{head} {flag_styled} {body}"


def _on_role_error(event: dict[str, Any], obs: StdioObserver) -> str:
    line = f"{_INDENT_ROLE}[{event.get('role')}] ERROR {event.get('message')!r}"
    return obs._c(_RED + _BOLD, line)


_HANDLERS: dict[str, Any] = {
    "task.started": _on_task_started,
    "task.completed": _on_task_completed,
    "planner.started": _on_planner_started,
    "planner.completed": _on_planner_completed,
    "sprint.started": _on_sprint_started,
    "sprint.completed": _on_sprint_completed,
    "contract.written": _on_contract_written,
    "generator.started": _on_generator_started,
    "generator.completed": _on_generator_completed,
    "evaluator.started": _on_evaluator_started,
    "evaluator.completed": _on_evaluator_completed,
    "role.session.opened": _on_role_session_opened,
    "role.session.recovered": _on_role_session_recovered,
    "role.session.closed": _on_role_session_closed,
    "role.text": _on_role_text,
    "role.tool.start": _on_role_tool_start,
    "role.tool.result": _on_role_tool_result,
    "role.error": _on_role_error,
}
