"""Orientation ritual (Spec 03 stage 3b, decision #3 / acceptance #15).

The orientation ritual is hybrid by design: a deterministic ``gather``
step (read AGENTS.md, validator findings, recent progress, active
exec-plan) followed by an optional LLM ``summariser`` step. The
``--no-ai-orientation`` mode is encoded by leaving
``OrientationConfig.summariser`` as ``None``; this module never imports
a model client.

The orchestrator (``run_session``) calls ``run_orientation`` once per
session at the ``starting -> orienting`` boundary, prepends the
returned brief to the transcript via ``to_user_message``, and refuses
to enter ``working`` if any finding has severity ``"blocking"`` (#15).
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from dream.engine._messages import ConversationMessage, TextBlock

ValidatorSeverity = Literal["info", "warning", "blocking"]


@dataclass(frozen=True)
class ValidatorFinding:
    severity: ValidatorSeverity
    code: str
    message: str
    path: str | None = None


def _format_finding(f: ValidatorFinding) -> str:
    suffix = f" ({f.path})" if f.path else ""
    return f"- [{f.severity}] {f.code}: {f.message}{suffix}"


def _format_bullets(items: list[str]) -> str:
    return "\n".join(f"- {it}" for it in items) if items else "(none)"


@dataclass(frozen=True)
class OrientationBrief:
    repo_summary: str
    progress_tail: str
    active_exec_plan: str
    validator_findings: list[ValidatorFinding] = field(default_factory=list)
    core_beliefs_digest: list[str] = field(default_factory=list)
    house_rules: list[str] = field(default_factory=list)
    llm_summary: str | None = None

    @property
    def has_blocking_findings(self) -> bool:
        return any(f.severity == "blocking" for f in self.validator_findings)

    def to_user_message(self) -> ConversationMessage:
        findings_block = (
            "\n".join(_format_finding(f) for f in self.validator_findings)
            if self.validator_findings
            else "(none)"
        )
        sections: list[str] = [
            "# Orientation brief",
            "",
            "## Repo",
            self.repo_summary or "(none)",
            "",
            "## Recent progress",
            self.progress_tail or "(none)",
            "",
            "## Active exec-plan",
            self.active_exec_plan or "(none)",
            "",
            "## Validator findings",
            findings_block,
            "",
            "## Core beliefs",
            _format_bullets(self.core_beliefs_digest),
            "",
            "## House rules",
            _format_bullets(self.house_rules),
        ]
        if self.llm_summary is not None:
            sections.extend(["", "## LLM summary", self.llm_summary])
        text = "\n".join(sections)
        return ConversationMessage(role="user", content=[TextBlock(text=text)])


@dataclass
class OrientationConfig:
    gather: Callable[[], Awaitable[OrientationBrief]]
    summariser: Callable[[OrientationBrief], Awaitable[str]] | None = None


async def run_orientation(config: OrientationConfig) -> OrientationBrief:
    """Run the orientation ritual: gather + optional summary.

    Short-circuits the summariser when the gather step already
    surfaced a blocking finding (the session will abort, so the LLM
    round-trip would be wasted). A summariser exception is best-effort:
    we return the brief with ``llm_summary=None`` rather than failing
    the whole session for a flaky model.
    """
    brief = await config.gather()
    if brief.has_blocking_findings or config.summariser is None:
        return brief
    summary: str | None = None
    with contextlib.suppress(Exception):
        summary = await config.summariser(brief)
    if summary is None:
        return brief
    return replace(brief, llm_summary=summary)


__all__ = [
    "OrientationBrief",
    "OrientationConfig",
    "ValidatorFinding",
    "ValidatorSeverity",
    "run_orientation",
]
