"""Handoff event helper for cross-role transitions.

Pinned by spec 10 §"Handoff event":

- ``type``: ``"handoff.{from}_to_{to}"`` where both roles are lowercased
  names from the documented role set.
- ``ts``: iso8601 UTC.
- ``artefacts``: a non-empty list of ``{kind, path|ref}`` pointers — the
  next session locates its inputs *only* through these (repo-only rule).

Empty artefact lists are structurally invalid and rejected at build time;
this saves a callsite from emitting a handoff event the next agent has no
way to consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

__all__ = ["ArtefactKind", "HandoffArtefact", "Role", "handoff_event"]


# Spec 10 §1 enumerates the role set.
Role = Literal["runner", "planner", "generator", "evaluator", "reviewer"]
_VALID_ROLES: frozenset[str] = frozenset(
    {"runner", "planner", "generator", "evaluator", "reviewer"}
)

ArtefactKind = Literal["spec", "ledger", "contract", "eval", "diff"]
_VALID_ARTEFACT_KINDS: frozenset[str] = frozenset(
    {"spec", "ledger", "contract", "eval", "diff"}
)


@dataclass(frozen=True)
class HandoffArtefact:
    """A pointer to one input the next session needs.

    Exactly one of ``path`` (repo-relative file path) or ``ref`` (URL-like
    pointer, e.g. ``sidecar://<id>``) must be supplied — both omitted
    means the next session can't find the artefact, both supplied is
    ambiguous.
    """

    kind: str
    path: str | None = None
    ref: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _VALID_ARTEFACT_KINDS:
            raise ValueError(
                f"unknown artefact kind {self.kind!r}; expected one of "
                f"{sorted(_VALID_ARTEFACT_KINDS)}"
            )
        if not self.path and not self.ref:
            raise ValueError("HandoffArtefact requires one of path|ref to be set")
        if self.path and self.ref:
            raise ValueError("HandoffArtefact accepts path OR ref, not both")

    def to_dict(self) -> dict[str, str]:
        out: dict[str, str] = {"kind": self.kind}
        if self.path is not None:
            out["path"] = self.path
        if self.ref is not None:
            out["ref"] = self.ref
        return out


def handoff_event(
    *,
    from_role: str,
    to_role: str,
    artefacts: list[HandoffArtefact],
) -> dict[str, Any]:
    """Build the jsonl payload for a cross-role transition.

    Raises:
        ValueError: if either role is unknown or ``artefacts`` is empty.
    """
    if from_role not in _VALID_ROLES:
        raise ValueError(
            f"unknown from_role {from_role!r}; expected one of {sorted(_VALID_ROLES)}"
        )
    if to_role not in _VALID_ROLES:
        raise ValueError(
            f"unknown to_role {to_role!r}; expected one of {sorted(_VALID_ROLES)}"
        )
    if not artefacts:
        # Spec 10: "(≥1 pointer)". An empty handoff is structurally invalid —
        # the next session has nothing to read.
        raise ValueError("handoff_event requires at least one artefact pointer")

    return {
        "type": f"handoff.{from_role}_to_{to_role}",
        "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "from_role": from_role,
        "to_role": to_role,
        "artefacts": [a.to_dict() for a in artefacts],
    }
