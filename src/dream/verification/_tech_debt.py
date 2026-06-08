"""Auto-file tech debt from verification failures (Spec 12e, tech-debt half).

When a verification step fails in a way an operator has taught the harness to
recognise as a *missing capability*, file a one-line bullet to the #07 tech-debt
tracker (the harness never acts on it — the operator triages). The matcher is
**fully operator-declared**: the harness ships no built-in patterns, so by
default nothing is filed (the spec's "conservative — false negatives preferred"
taken to its floor). Operators opt in via ``.harness/tech-debt-matchers.toml``:

    [[matcher]]
    pattern = "ModuleNotFoundError: No module named '([\\w.]+)'"
    missing = "python module not installed: {1}"

``missing`` is a template where ``{0}`` is the whole match and ``{1}``/``{2}``…
are capture groups.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dream.tasks._tech_debt import TechDebtEntry, append_tech_debt_entry
from dream.verification._types import RepoVerificationStep, VerificationReport

_GROUP_TOKEN = re.compile(r"\{(\d+)\}")
_WHITESPACE = re.compile(r"\s+")


class TechDebtMatcherError(ValueError):
    """Raised when ``tech-debt-matchers.toml`` is malformed."""


@dataclass(frozen=True)
class Matcher:
    """One operator-declared rule: a regex + a ``missing`` template."""

    pattern: re.Pattern[str]
    missing_template: str


@dataclass(frozen=True)
class FilingResult:
    """Outcome of auto-filing: what was filed, and what matched nothing.

    ``unmatched`` is the spec's "info event" — the failures the conservative
    matcher did not recognise, surfaced as data for the caller to log.
    """

    filed: tuple[TechDebtEntry, ...]
    unmatched: tuple[RepoVerificationStep, ...]


def parse_matchers(text: str) -> list[Matcher]:
    """Parse the matcher config body into rules."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TechDebtMatcherError(f"invalid tech-debt matcher TOML: {exc}") from exc

    raw = data.get("matcher", [])
    if not isinstance(raw, list):
        raise TechDebtMatcherError("'[[matcher]]' must be an array of tables")
    return [_matcher_from_table(item) for item in raw]


def load_matchers(path: Path) -> list[Matcher]:
    """Read + parse the matcher config; a missing file yields no rules."""
    if not path.is_file():
        return []
    return parse_matchers(path.read_text(encoding="utf-8"))


def match_failure(step: RepoVerificationStep, matchers: list[Matcher]) -> str | None:
    """First rule that matches the step's output → its interpolated ``missing``.

    Searches ``stderr`` then ``stdout`` (combined). Returns ``None`` when no rule
    matches — the caller files nothing.
    """
    haystack = f"{step.stderr}\n{step.stdout}"
    for matcher in matchers:
        found = matcher.pattern.search(haystack)
        if found is not None:
            return _interpolate(matcher.missing_template, found)
    return None


def file_verification_tech_debt(
    report: VerificationReport,
    *,
    task_id: str | None,
    tracker_root: str | Path,
    report_path: str,
    matchers: list[Matcher],
    now: datetime | None = None,
) -> FilingResult:
    """Append a tech-debt bullet for each *recognised* verification failure.

    Every failed/error step is matched against ``matchers``; a match files one
    :class:`TechDebtEntry` (``source="verification.failure"``) to the #07 tracker
    and unrecognised failures are returned in ``unmatched`` (filed nothing).
    """
    stamp = now or datetime.now(UTC)
    filed: list[TechDebtEntry] = []
    unmatched: list[RepoVerificationStep] = []
    for step in report.failures:
        missing = match_failure(step, matchers)
        if missing is None:
            unmatched.append(step)
            continue
        entry = TechDebtEntry(
            ts=stamp,
            source="verification.failure",
            missing=_single_line(missing),
            evidence=_single_line(f"{report_path}#{step.name or step.command}"),
            task_id=task_id,
        )
        append_tech_debt_entry(tracker_root, entry)
        filed.append(entry)
    return FilingResult(filed=tuple(filed), unmatched=tuple(unmatched))


def _single_line(text: str) -> str:
    """Collapse whitespace to a single line (tracker bullets are one line)."""
    return _WHITESPACE.sub(" ", text).strip()


def _matcher_from_table(item: Any) -> Matcher:
    if not isinstance(item, dict):
        raise TechDebtMatcherError(
            f"each '[[matcher]]' must be a table, got {type(item).__name__}"
        )
    pattern = item.get("pattern")
    missing = item.get("missing")
    if not (isinstance(pattern, str) and pattern):
        raise TechDebtMatcherError(f"matcher missing 'pattern': {item!r}")
    if not (isinstance(missing, str) and missing):
        raise TechDebtMatcherError(f"matcher missing 'missing': {item!r}")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise TechDebtMatcherError(f"invalid matcher regex {pattern!r}: {exc}") from exc
    return Matcher(pattern=compiled, missing_template=missing)


def _interpolate(template: str, match: re.Match[str]) -> str:
    """Replace ``{N}`` tokens with match group N; leave an out-of-range token as-is."""

    def _sub(token: re.Match[str]) -> str:
        try:
            value = match.group(int(token.group(1)))
        except IndexError:  # template referenced a group the regex doesn't have
            return token.group(0)
        return value if value is not None else token.group(0)

    return _GROUP_TOKEN.sub(_sub, template)


__all__ = [
    "FilingResult",
    "Matcher",
    "TechDebtMatcherError",
    "file_verification_tech_debt",
    "load_matchers",
    "match_failure",
    "parse_matchers",
]
