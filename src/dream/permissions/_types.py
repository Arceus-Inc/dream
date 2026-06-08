"""Value objects + enums for the permission decision core (Spec 13A).

Pure data with no IO and no dispatch. A :class:`PermissionRequest` describes
what a single tool call will do; a :class:`Policy` is the active sandbox
posture plus operator rules; ``evaluate`` (in :mod:`dream.permissions._checker`)
maps the two to a :class:`PermissionDecision`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path


class SandboxTier(IntEnum):
    """The four sandbox postures, ordered by capability.

    ``IntEnum`` makes the subset relation a plain comparison:
    ``READ_ONLY < REPO_WRITE < REPO_WRITE_NET < UNRESTRICTED``.
    """

    READ_ONLY = 0
    REPO_WRITE = 1
    REPO_WRITE_NET = 2
    UNRESTRICTED = 3


class Effect(Enum):
    """A tier-gated side effect, carrying the minimum tier that permits it.

    Reads are always allowed (subject to the credential guard) and need no
    tier, so only the two gated effects are modelled here. The required tier
    lives on the member itself — ``Effect.WRITE.required_tier`` — rather than
    in an external lookup table.
    """

    WRITE = ("write", SandboxTier.REPO_WRITE)
    NETWORK = ("network", SandboxTier.REPO_WRITE_NET)

    label: str
    required_tier: SandboxTier

    def __init__(self, label: str, required_tier: SandboxTier) -> None:
        self.label = label
        self.required_tier = required_tier


class Outcome(Enum):
    """The three permission outcomes.

    ``ASK`` means "no rule decided — an approver could unblock this"; with no
    approver wired (autonomous run) the caller collapses it to a deny.
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class PermissionRequest:
    """What a single tool call will do.

    Populated by the dispatch layer (later wiring); the checker never inspects
    tool internals, only this explicit description.
    """

    tool_name: str
    is_read_only: bool
    target_paths: tuple[Path, ...] = ()
    command: str | None = None
    network_host: str | None = None


@dataclass(frozen=True)
class PermissionDecision:
    """The checker's verdict: an outcome, a human reason, and the deciding rule.

    ``rule`` is a stable observability label for *which* pipeline step decided
    (e.g. ``"credential_guard"``); it is never dispatched on.
    """

    outcome: Outcome
    reason: str
    rule: str

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW


@dataclass(frozen=True)
class PathRule:
    """An operator path allow/deny rule (OpenHarness ``PathRule`` shape)."""

    pattern: str
    allow: bool


@dataclass(frozen=True)
class Policy:
    """The active posture + operator rules the checker evaluates against.

    ``required_tier`` implements the trust ramp: a tool absent from the map is
    treated as ``READ_ONLY`` (newly discovered, untrusted) until an operator
    promotes it. ``credential_extra`` is operator *add-only* — it can extend
    the non-disableable credential guard but never shrink it.
    """

    tier: SandboxTier
    cwd: Path
    required_tier: Mapping[str, SandboxTier] = field(default_factory=dict)
    tool_deny: frozenset[str] = frozenset()
    tool_allow: frozenset[str] = frozenset()
    path_deny: tuple[PathRule, ...] = ()
    command_deny: tuple[re.Pattern[str], ...] = ()
    extra_allowed: tuple[Path, ...] = ()
    credential_extra: tuple[str, ...] = ()
