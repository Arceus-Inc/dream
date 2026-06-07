"""Typed jsonl event log for context operations (Spec 04 stage 4a).

Every context operation (compaction trigger/completion, reset, handoff,
tool-output offload, skill load) is appended to a per-session jsonl log
as a typed event. The agent itself can read this log back via
``read_my_context_log`` (Spec 04 #14) so it can prefer compactable
content instead of re-discovering what's already happened to its window.

Why typed dataclasses, not dicts: consumers branch on ``isinstance`` —
adding a new event means adding a new dataclass, never a free-form
``extra`` field. The catalogue is fixed by Spec 04 `## Artefact shapes`.

The jsonl codec uses the class-level ``name`` ClassVar as the
discriminator: ``to_jsonl_line`` injects it; ``from_jsonl_line`` reads it
and looks up the registered dataclass. Unknown names raise — silent drop
would let a corrupted log hide bugs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, ClassVar, Literal, TextIO

CompactTrigger = Literal["auto", "manual", "reactive"]
CompactTier = Literal["microcompact", "full"]


@dataclass(frozen=True)
class ContextCompactionTriggered:
    """A compaction trigger fired (auto threshold, manual, or reactive PTL)."""

    utilisation: float
    trigger: CompactTrigger
    at: str = ""  # iso-8601, optional
    name: ClassVar[str] = "context.compaction.triggered"


@dataclass(frozen=True)
class ContextCompactionCompleted:
    """A compaction finished; records which tier ran and what room it freed."""

    tier: CompactTier
    preserved_attachments: int
    resulting_utilisation: float
    at: str = ""
    name: ClassVar[str] = "context.compaction.completed"


@dataclass(frozen=True)
class ContextResetTriggered:
    """The unproductive-compactions counter (or compactor-failure counter) hit 2."""

    reason: str
    at: str = ""
    name: ClassVar[str] = "context.reset.triggered"


@dataclass(frozen=True)
class ContextHandoffWritten:
    """A handoff file landed on disk; the session will now seal as done-handed-off."""

    path: str
    at: str = ""
    name: ClassVar[str] = "context.handoff.written"


@dataclass(frozen=True)
class ContextToolOutputOffloaded:
    """A tool result over the inline limit was spilled to sidecar scratch."""

    tool_name: str
    tool_use_id: str
    offloaded_to: str
    original_size_bytes: int
    at: str = ""
    name: ClassVar[str] = "context.tool_output.offloaded"


@dataclass(frozen=True)
class ContextSkillLoaded:
    """A skill body loaded into the session (progressive disclosure, Spec 04 #12)."""

    skill_name: str
    at: str = ""
    name: ClassVar[str] = "context.skill.loaded"


ContextEvent = (
    ContextCompactionTriggered
    | ContextCompactionCompleted
    | ContextResetTriggered
    | ContextHandoffWritten
    | ContextToolOutputOffloaded
    | ContextSkillLoaded
)


_EVENT_TYPES: tuple[type[ContextEvent], ...] = (
    ContextCompactionTriggered,
    ContextCompactionCompleted,
    ContextResetTriggered,
    ContextHandoffWritten,
    ContextToolOutputOffloaded,
    ContextSkillLoaded,
)

_EVENT_BY_NAME: dict[str, type[ContextEvent]] = {cls.name: cls for cls in _EVENT_TYPES}


# --- jsonl codec -------------------------------------------------------------


def to_jsonl_line(event: ContextEvent) -> str:
    """Serialise one event as a single jsonl line (no trailing newline)."""
    payload: dict[str, Any] = {"name": event.name, **asdict(event)}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def from_jsonl_line(line: str) -> ContextEvent:
    """Parse one jsonl line back into a typed event; raises on malformed input."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed jsonl line: {line!r}") from exc
    if not isinstance(payload, dict) or "name" not in payload:
        raise ValueError(f"jsonl payload missing 'name': {line!r}")
    name = payload["name"]
    if not isinstance(name, str):
        raise ValueError(f"jsonl 'name' must be a string: {line!r}")
    cls = _EVENT_BY_NAME.get(name)
    if cls is None:
        raise ValueError(f"unknown context event name: {name!r}")
    field_names = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in payload.items() if k in field_names}
    try:
        return cls(**kwargs)
    except TypeError as exc:
        # Missing/extra required fields surface as ValueError so every
        # malformed line is uniform for read_context_log callers.
        raise ValueError(f"malformed jsonl line for {name!r}: {line!r}") from exc


# --- append-only file sink ---------------------------------------------------


class ContextLogWriter:
    """Append-only jsonl writer; flushes each ``emit`` so crash-resume can re-read."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = path.open("a", encoding="utf-8")

    def emit(self, event: ContextEvent) -> None:
        self._fh.write(to_jsonl_line(event))
        self._fh.write("\n")
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


def read_context_log(path: Path) -> list[ContextEvent]:
    """Parse a context jsonl file into typed events; missing file yields ``[]``."""
    if not path.exists():
        return []
    events: list[ContextEvent] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        events.append(from_jsonl_line(raw))
    return events


# Spec 04 acceptance #14: the agent itself can read its own context log so it
# can prefer compactable content; same function, named per the spec for the
# agent-facing tool surface.
read_my_context_log = read_context_log


__all__ = [
    "CompactTier",
    "CompactTrigger",
    "ContextCompactionCompleted",
    "ContextCompactionTriggered",
    "ContextEvent",
    "ContextHandoffWritten",
    "ContextLogWriter",
    "ContextResetTriggered",
    "ContextSkillLoaded",
    "ContextToolOutputOffloaded",
    "from_jsonl_line",
    "read_context_log",
    "read_my_context_log",
    "to_jsonl_line",
]
