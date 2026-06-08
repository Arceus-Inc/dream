"""Run-time observer for ``Harness.run_task`` and ``run_role``.

The runner is silent by default: every meaningful boundary is captured
into the returned ``RunTaskResult.events`` tuple but nothing is written
anywhere. For interactive operator use that's the wrong default — when
you type ``await harness.run_task(intent="…")`` you want to *see* the
planner draft a spec, the negotiation rounds, every tool the generator
calls, and the evaluator's verdict as they happen.

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
* ``negotiation.imposed`` — sprint_number, rounds (cap hit)
* ``contract.written`` — sprint_number, path
* ``generator.started``— sprint_number, step_id, has_contract
* ``generator.completed`` — sprint_number, step_id
* ``evaluator.started``— sprint_number, step_id
* ``evaluator.completed`` — sprint_number, outcome, score, notes
* ``role.session.opened`` — role, session_id
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


def _truncate(text: str, *, limit: int = 160) -> str:
    """One-line preview of an arbitrary string."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


@dataclass
class StdioObserver:
    """Default observer: one tagged line per event on ``sys.stdout``.

    Pass ``stream=sys.stderr`` (or any open text file) to redirect. The
    ``role_text_buffering`` flag controls whether streaming text deltas
    are accumulated across a role session and printed in one block on
    ``role.session.closed`` (``True``, the default — readable) or
    written verbatim as they arrive (``False`` — useful when piping
    into another tool).
    """

    stream: TextIO = field(default_factory=lambda: sys.stdout)
    role_text_buffering: bool = True

    _text_buffers: dict[str, list[str]] = field(default_factory=dict)

    def on_event(self, event: dict[str, Any]) -> None:
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


def _format_default(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[?] {event.get('kind', 'unknown')} {event}"


def _on_task_started(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[task] start task_id={event.get('task_id')!r} intent={_truncate(str(event.get('intent', '')))!r}"


def _on_task_completed(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[task] done task_id={event.get('task_id')!r} sprints={event.get('sprint_count')}"


def _on_planner_started(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[planner] drafting spec for {event.get('task_id')!r}"


def _on_planner_completed(event: dict[str, Any], _obs: StdioObserver) -> str:
    return (
        f"[planner] done — wrote {event.get('spec_path')} + "
        f"{event.get('ledger_path')} ({event.get('step_count')} steps)"
    )


def _on_sprint_started(event: dict[str, Any], _obs: StdioObserver) -> str:
    return (
        f"[sprint {event.get('sprint_number')}] start step={event.get('step_id')!r} "
        f"— {_truncate(str(event.get('step_description', '')))}"
    )


def _on_sprint_completed(event: dict[str, Any], _obs: StdioObserver) -> str:
    outcome = event.get("outcome")
    suffix = f"outcome={outcome}" if outcome else "outcome=(evaluator disabled)"
    return f"[sprint {event.get('sprint_number')}] done step={event.get('step_id')!r} {suffix}"


def _on_negotiation_imposed(event: dict[str, Any], _obs: StdioObserver) -> str:
    return (
        f"[sprint {event.get('sprint_number')}] negotiation cap hit after "
        f"{event.get('rounds')} rounds — contract imposed"
    )


def _on_contract_written(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[sprint {event.get('sprint_number')}] contract written → {event.get('path')}"


def _on_generator_started(event: dict[str, Any], _obs: StdioObserver) -> str:
    flag = "with contract" if event.get("has_contract") else "no contract (evaluator off)"
    return (
        f"[sprint {event.get('sprint_number')}] generator start "
        f"step={event.get('step_id')!r} ({flag})"
    )


def _on_generator_completed(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[sprint {event.get('sprint_number')}] generator done step={event.get('step_id')!r}"


def _on_evaluator_started(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[sprint {event.get('sprint_number')}] evaluator start step={event.get('step_id')!r}"


def _on_evaluator_completed(event: dict[str, Any], _obs: StdioObserver) -> str:
    notes = event.get("notes") or ""
    notes_part = f" notes={_truncate(notes)!r}" if notes else ""
    return (
        f"[sprint {event.get('sprint_number')}] evaluator done "
        f"outcome={event.get('outcome')!r} score={event.get('score')}{notes_part}"
    )


def _on_role_session_opened(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[{event.get('role')}] session open id={event.get('session_id')!r}"


def _on_role_session_closed(event: dict[str, Any], obs: StdioObserver) -> str:
    role = str(event.get("role", "?"))
    suffix = ""
    if obs.role_text_buffering:
        chunks = obs._text_buffers.pop(role, None)
        if chunks:
            joined = "".join(chunks).strip()
            if joined:
                suffix = f"\n[{role}] reply: {_truncate(joined, limit=400)}"
    return (
        f"[{role}] session close id={event.get('session_id')!r} "
        f"cost_usd={event.get('cost_usd')}{suffix}"
    )


def _on_role_text(event: dict[str, Any], obs: StdioObserver) -> str | None:
    role = str(event.get("role", "?"))
    text = str(event.get("text", ""))
    if obs.role_text_buffering:
        obs._text_buffers.setdefault(role, []).append(text)
        return None
    return f"[{role}] {text}"


def _on_role_tool_start(event: dict[str, Any], _obs: StdioObserver) -> str:
    tool = event.get("tool", "?")
    input_preview = _truncate(repr(event.get("input", {})), limit=120)
    return f"[{event.get('role')}] tool→ {tool} {input_preview}"


def _on_role_tool_result(event: dict[str, Any], _obs: StdioObserver) -> str:
    flag = "error" if event.get("is_error") else "ok"
    preview = _truncate(str(event.get("content_preview", "")))
    return f"[{event.get('role')}] tool← {event.get('tool')} [{flag}] {preview}"


def _on_role_error(event: dict[str, Any], _obs: StdioObserver) -> str:
    return f"[{event.get('role')}] ERROR {event.get('message')!r}"


_HANDLERS: dict[str, Any] = {
    "task.started": _on_task_started,
    "task.completed": _on_task_completed,
    "planner.started": _on_planner_started,
    "planner.completed": _on_planner_completed,
    "sprint.started": _on_sprint_started,
    "sprint.completed": _on_sprint_completed,
    "negotiation.imposed": _on_negotiation_imposed,
    "contract.written": _on_contract_written,
    "generator.started": _on_generator_started,
    "generator.completed": _on_generator_completed,
    "evaluator.started": _on_evaluator_started,
    "evaluator.completed": _on_evaluator_completed,
    "role.session.opened": _on_role_session_opened,
    "role.session.closed": _on_role_session_closed,
    "role.text": _on_role_text,
    "role.tool.start": _on_role_tool_start,
    "role.tool.result": _on_role_tool_result,
    "role.error": _on_role_error,
}
