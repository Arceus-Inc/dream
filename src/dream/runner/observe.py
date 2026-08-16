"""Observers and token metering for ``run_task`` / ``run_role``.

Ships:

* :class:`StdioObserver` — default human-readable progress lines
* :class:`CapturingObserver` — test helper that records typed events
* :class:`UsageMeter` — wraps an observer and folds role-session usage
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TextIO

from dream.engine._cost import UsageSnapshot
from dream.runner.events import (
    ContractWritten,
    EvaluatorCompleted,
    EvaluatorStarted,
    GeneratorCompleted,
    GeneratorStarted,
    HeadRetry,
    PlannerCompleted,
    PlannerSkipped,
    PlannerStarted,
    RoleError,
    RoleSessionClosed,
    RoleSessionOpened,
    RoleSessionRecovered,
    RoleText,
    RoleToolResult,
    RoleToolStart,
    RunTaskEvent,
    RunTaskObserver,
    SprintCompleted,
    SprintEscalated,
    SprintStarted,
    TaskCompleted,
    TaskStarted,
)

__all__ = [
    "BLUE",
    "BOLD",
    "CYAN",
    "DIM",
    "GREEN",
    "RED",
    "RESET",
    "YELLOW",
    "CapturingObserver",
    "StdioObserver",
    "UsageMeter",
    "c",
    "use_colour",
]

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
CYAN = "\x1b[36m"

_ROLE_COLOUR: dict[str, str] = {
    "planner": CYAN,
    "generator": GREEN,
    "evaluator": YELLOW,
}

_OUTCOME_COLOUR: dict[str, str] = {
    "pass": GREEN,
    "needs-changes": YELLOW,
    "fail": RED,
}

_INDENT_ROLE = "  "
_INDENT_TOOL = "    "


def use_colour(stream: TextIO) -> bool:
    """Return True only when ``stream`` is a real terminal and ``NO_COLOR`` is unset."""
    if os.environ.get("NO_COLOR"):
        return False
    return stream.isatty()


def c(code: str, text: str, *, use: bool) -> str:
    """Wrap ``text`` in ``code`` + reset, or return as-is when ``use`` is False."""
    if not use or not code:
        return text
    return f"{code}{text}{RESET}"


def _truncate(text: str, *, limit: int = 160) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _short_path(p: object) -> str:
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


def _format_tool_input(value: object) -> str:
    if not isinstance(value, Mapping):
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


def _outcome_glyph(outcome: str) -> str:
    if outcome == "pass":
        return "✓"
    if outcome == "fail":
        return "✗"
    return "▲"


@dataclass
class StdioObserver:
    """Default observer: one styled line per event on ``sys.stdout``."""

    stream: TextIO = field(default_factory=lambda: sys.stdout)
    role_text_buffering: bool = True
    _text_buffers: dict[str, list[str]] = field(default_factory=dict, init=False, repr=False)
    _colour: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._colour = use_colour(self.stream)

    def _c(self, code: str, text: str) -> str:
        return c(code, text, use=self._colour)

    def on_event(self, event: RunTaskEvent) -> None:
        line = self._format(event)
        if line is None:
            return
        self.stream.write(line)
        if not line.endswith("\n"):
            self.stream.write("\n")
        self.stream.flush()

    def _format(self, event: RunTaskEvent) -> str | None:
        match event:
            case TaskStarted():
                line = (
                    f"▶ [task] start task_id={event.task_id!r} "
                    f"intent={_truncate(event.intent)!r}"
                )
                return self._c(BOLD + BLUE, line)
            case TaskCompleted():
                line = (
                    f"✓ [task] done task_id={event.task_id!r} "
                    f"sprints={event.sprint_count}"
                )
                return self._c(BOLD + BLUE, line)
            case PlannerStarted():
                return self._c(CYAN, f"◇ [planner] drafting spec for {event.task_id!r}")
            case PlannerCompleted():
                line = (
                    f"✓ [planner] done — wrote {_short_path(event.spec_path)} + "
                    f"{_short_path(event.ledger_path)} ({event.step_count} steps)"
                )
                return self._c(CYAN, line)
            case PlannerSkipped():
                reason = _truncate(event.reason, limit=80)
                return self._c(DIM, f"[planner] skipped {reason}")
            case SprintStarted():
                line = (
                    f"\n▶ [sprint {event.sprint_number}] start "
                    f"step={event.step_id!r} — "
                    f"{_truncate(event.step_description)}"
                )
                return self._c(BOLD + BLUE, line)
            case SprintCompleted():
                outcome = event.outcome
                if outcome:
                    glyph = _outcome_glyph(outcome)
                    colour = _OUTCOME_COLOUR.get(outcome, "")
                    suffix = self._c(colour, f"outcome={outcome}")
                else:
                    glyph = "·"
                    suffix = "outcome=(evaluator disabled)"
                line = (
                    f"{glyph} [sprint {event.sprint_number}] done "
                    f"step={event.step_id!r} {suffix}"
                )
                return self._c(BOLD, line)
            case SprintEscalated():
                n = event.sprint_number if event.sprint_number is not None else "?"
                reason = _truncate(event.reason, limit=80)
                line = f"[sprint {n}] escalated step={event.step_id} {reason}"
                return self._c(RED + BOLD, line)
            case ContractWritten():
                line = (
                    f"◇ [sprint {event.sprint_number}] contract written → "
                    f"{_short_path(event.path)}"
                )
                return self._c(CYAN, line)
            case GeneratorStarted():
                flag = (
                    "with contract"
                    if event.has_contract
                    else "no contract (evaluator off)"
                )
                line = (
                    f"▶ [sprint {event.sprint_number}] generator start "
                    f"step={event.step_id!r} ({flag})"
                )
                return self._c(GREEN, line)
            case GeneratorCompleted():
                line = (
                    f"✓ [sprint {event.sprint_number}] generator done "
                    f"step={event.step_id!r}"
                )
                return self._c(GREEN, line)
            case EvaluatorStarted():
                line = (
                    f"▶ [sprint {event.sprint_number}] evaluator start "
                    f"step={event.step_id!r}"
                )
                return self._c(YELLOW, line)
            case EvaluatorCompleted():
                outcome = event.outcome
                glyph = _outcome_glyph(outcome)
                colour = _OUTCOME_COLOUR.get(outcome, YELLOW)
                notes_part = f" notes={_truncate(event.notes)!r}" if event.notes else ""
                line = (
                    f"{glyph} [sprint {event.sprint_number}] evaluator done "
                    f"outcome={outcome!r} score={event.score}{notes_part}"
                )
                return self._c(colour, line)
            case HeadRetry():
                error = _truncate(event.error, limit=80)
                line = f"{_INDENT_ROLE}[{event.role}] retry#{event.attempt} {error}"
                return self._c(YELLOW, line)
            case RoleSessionOpened():
                colour = _ROLE_COLOUR.get(event.role, "")
                line = (
                    f"{_INDENT_ROLE}╭ [{event.role}] session open "
                    f"id={event.session_id!r}"
                )
                return self._c(DIM + colour, line)
            case RoleSessionRecovered():
                colour = _ROLE_COLOUR.get(event.role, "")
                line = (
                    f"{_INDENT_ROLE}[{event.role}] session recovered "
                    f"id={event.session_id!r} requested={event.requested_session_id!r} "
                    f"reason={event.reason!r} action={event.action!r} "
                    f"snapshot_preserved={event.snapshot_preserved}"
                )
                return self._c(YELLOW + colour, line)
            case RoleSessionClosed():
                return self._format_role_session_closed(event)
            case RoleText():
                if self.role_text_buffering:
                    self._text_buffers.setdefault(event.role, []).append(event.text)
                    return None
                colour = _ROLE_COLOUR.get(event.role, "")
                return self._c(colour, f"{_INDENT_ROLE}[{event.role}] {event.text}")
            case RoleToolStart():
                colour = _ROLE_COLOUR.get(event.role, "")
                input_preview = _format_tool_input(event.input)
                line = f"{_INDENT_TOOL}[{event.role}] tool→ {event.tool} {input_preview}"
                return self._c(colour, line)
            case RoleToolResult():
                flag = "error" if event.is_error else "ok"
                preview = _truncate(event.content)
                role_colour = _ROLE_COLOUR.get(event.role, "")
                flag_colour = RED if event.is_error else GREEN
                head = self._c(role_colour, f"{_INDENT_TOOL}[{event.role}] tool← {event.tool}")
                flag_styled = self._c(flag_colour, f"[{flag}]")
                body = self._c(DIM, preview) if self._colour else preview
                return f"{head} {flag_styled} {body}"
            case RoleError():
                line = f"{_INDENT_ROLE}[{event.role}] ERROR {event.message!r}"
                return self._c(RED + BOLD, line)

    def _format_role_session_closed(self, event: RoleSessionClosed) -> str:
        colour = _ROLE_COLOUR.get(event.role, "")
        cost_str = f"${event.cost_usd:.4f}" if event.cost_usd > 0 else "$0"
        reply_block = ""
        if self.role_text_buffering:
            chunks = self._text_buffers.pop(event.role, None)
            if chunks:
                joined = "".join(chunks).strip()
                if joined:
                    reply = _truncate(joined, limit=400)
                    reply_block = (
                        f"\n{_INDENT_ROLE}{self._c(colour, f'[{event.role}] reply:')} "
                        f"{self._c(DIM, reply)}"
                    )
        line = (
            f"{_INDENT_ROLE}╰ [{event.role}] session close "
            f"id={event.session_id!r} cost_usd={event.cost_usd} ({cost_str})"
        )
        return self._c(DIM + colour, line) + reply_block


@dataclass
class CapturingObserver:
    """Test helper: records every event into ``events`` in order."""

    events: list[RunTaskEvent] = field(default_factory=list)

    def on_event(self, event: RunTaskEvent) -> None:
        self.events.append(event)


class UsageMeter:
    """Wraps an optional observer; accumulates per-model token usage."""

    def __init__(self, inner: RunTaskObserver | None = None) -> None:
        self._inner = inner
        self._by_model: dict[str, UsageSnapshot] = {}

    def on_event(self, event: RunTaskEvent) -> None:
        if isinstance(event, RoleSessionClosed) and event.model:
            self._by_model[event.model] = (
                self._by_model.get(event.model, UsageSnapshot()) + event.usage
            )
        if self._inner is not None:
            self._inner.on_event(event)

    @property
    def usage_by_model(self) -> Mapping[str, UsageSnapshot]:
        return dict(self._by_model)
