"""Spec 04 stage 4c — reset trigger + structured handoff writer.

Two reset paths feed one outcome:

- *Unproductive compactions* — a turn that ran a compaction but produced
  no ``step → done`` ledger transition; two consecutive ones means the
  compactor is reclaiming room but the agent is not advancing.
- *Compactor failures* — two consecutive failures of the compactor itself
  reset regardless of ledger advancement (the compactor is the only thing
  keeping the window open; if it dies, the session has to hand off).

On reset, :func:`write_handoff` lays down ``docs/sessions/handoff/{id}.md``
with the five Spec 04 sections plus pointers to the sealed jsonl and the
final checkpoint ref, atomically via :func:`dream.utils.fs.atomic_write_text`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.utils.fs import atomic_write_text

# --- counter -----------------------------------------------------------------


@dataclass
class UnproductiveCompactionTracker:
    """Two counters feeding one reset decision."""

    unproductive_count: int = 0
    compactor_failure_count: int = 0

    def record_turn(self, *, compacted: bool, productive: bool) -> None:
        """Update the ledger-based counter at end of turn.

        A productive turn (any ``step → done`` transition this turn) clears
        the unproductive counter, even if no compaction ran. The compactor-
        failure counter is independent — only :meth:`record_compactor_success`
        clears it.
        """
        if not compacted:
            if productive:
                self.unproductive_count = 0
            return
        if productive:
            self.unproductive_count = 0
        else:
            self.unproductive_count += 1

    def record_compactor_failure(self) -> None:
        self.compactor_failure_count += 1

    def record_compactor_success(self) -> None:
        self.compactor_failure_count = 0

    def should_reset(self) -> bool:
        return self.unproductive_count >= 2 or self.compactor_failure_count >= 2

    def reset_reason(self) -> str | None:
        """``None`` until :meth:`should_reset` is true; otherwise a stable tag."""
        # Compactor failures take precedence — they're a stronger signal that
        # the compactor itself is the problem.
        if self.compactor_failure_count >= 2:
            return "compactor_failures"
        if self.unproductive_count >= 2:
            return "unproductive_compactions"
        return None


# --- handoff shape -----------------------------------------------------------


@dataclass(frozen=True)
class HandoffSections:
    """The five Spec 04 sections plus the two recovery pointers."""

    why: str
    attempted: str
    still_open: str
    known_bad: str
    next_action: str
    sealed_jsonl_path: str = ""
    final_checkpoint_ref: str = ""


# --- writer / reader ---------------------------------------------------------


_HANDOFF_SUBDIR = ("docs", "sessions", "handoff")


def _handoff_path(*, root: Path, session_id: str) -> Path:
    if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
        # Session ids land directly in the on-disk filename, so any
        # path-segment character would escape the handoff subtree.
        raise ValueError(f"invalid session_id for handoff: {session_id!r}")
    return root.joinpath(*_HANDOFF_SUBDIR, f"{session_id}.md")


def write_handoff(
    *, root: Path, session_id: str, sections: HandoffSections
) -> Path:
    """Atomically write the handoff markdown; return the path written."""
    path = _handoff_path(root=root, session_id=session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _render(sections, session_id=session_id)
    atomic_write_text(path, body)
    return path


def read_handoff(*, root: Path, session_id: str) -> str | None:
    """Return the handoff markdown for ``session_id``, or ``None`` if absent."""
    path = _handoff_path(root=root, session_id=session_id)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _render(sections: HandoffSections, *, session_id: str) -> str:
    parts: list[str] = [
        f"# Session handoff: {session_id}",
        "",
        "# Why handing off",
        sections.why.rstrip(),
        "",
        "# What was attempted",
        sections.attempted.rstrip(),
        "",
        "# What is still open",
        sections.still_open.rstrip(),
        "",
        "# What is known not to work",
        sections.known_bad.rstrip(),
        "",
        "# Next concrete action",
        sections.next_action.rstrip(),
        "",
    ]
    if sections.sealed_jsonl_path or sections.final_checkpoint_ref:
        parts.append("# Recovery pointers")
        if sections.sealed_jsonl_path:
            parts.append(f"- Sealed jsonl: {sections.sealed_jsonl_path}")
        if sections.final_checkpoint_ref:
            parts.append(f"- Final checkpoint: {sections.final_checkpoint_ref}")
        parts.append("")
    return "\n".join(parts)


__all__: list[str] = [
    "HandoffSections",
    "UnproductiveCompactionTracker",
    "read_handoff",
    "write_handoff",
]
