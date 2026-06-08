"""Teammate identity + name sanitisation.

Spec 10 §"Teammate spawn config": ``agent_id = {sanitised name}@{sanitised
team}``. The sanitisers mirror OpenHarness's ``sanitizeName`` /
``sanitizeAgentName`` so existing team registries are readable, but the
storage location (worktree, not home dir) diverges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "TeammateIdentity",
    "sanitize_agent_name",
    "sanitize_team_name",
]

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")
_ALNUM = re.compile(r"[a-zA-Z0-9]")


def _sanitize_token(raw: str, *, label: str) -> str:
    if not raw:
        raise ValueError(f"{label} must not be empty")
    if not _ALNUM.search(raw):
        raise ValueError(
            f"{label} must produce at least one alphanumeric character "
            f"after sanitisation; got {raw!r}"
        )
    return _NON_ALNUM.sub("-", raw).lower()


def sanitize_team_name(name: str) -> str:
    """Lowercase + collapse non-alphanumerics to ``-``."""
    return _sanitize_token(name, label="team name")


def sanitize_agent_name(name: str) -> str:
    """Same as :func:`sanitize_team_name` — the ``@`` is part of the
    non-alnum class, so an agent name with an ``@`` becomes ambiguity-free."""
    return _sanitize_token(name, label="agent name")


@dataclass(frozen=True)
class TeammateIdentity:
    """Pinned identity for a teammate. Construct via :meth:`create`."""

    agent_id: str
    name: str
    team: str

    @classmethod
    def create(cls, *, name: str, team: str) -> "TeammateIdentity":
        sname = sanitize_agent_name(name)
        steam = sanitize_team_name(team)
        return cls(agent_id=f"{sname}@{steam}", name=sname, team=steam)
