"""Boot gates + resume scan for the long-running runtime (spec 15 P1 §1).

The gate order mirrors the REPL's session-start sequence so a headless
runtime gets exactly the same protections:

1. **Skills gate** (Spec 06 MUST #3) — a malformed SKILL.md blocks boot.
2. **Structural validation** (Spec 01 / left-over spec 05) — advisory;
   findings surface as warnings, never block (an embedded-SDK consumer
   repo doesn't carry the harness's own docs layout).
3. **Threat scan** (Spec 13E) — a worktree secret / world-writable file
   under ``docs/`` / eval-in-tool blocks boot.
4. **Resume scan** — every sidecar whose ``state.json`` still says
   ``running`` is an in-flight task a previous process left behind; the
   runtime surfaces these as resume candidates at boot. (Re-queue /
   adopt *policy* is the P3 watchdog's job — boot only reports.)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from dream.config.paths import DreamPaths
from dream.services.repo_validator import Finding, has_blocking, validate_repo
from dream.services.threat_scan import threat_scan
from dream.skills import validate_skills
from dream.state.sidecar import TaskState, read_state

__all__ = ["BootReport", "run_boot_gates", "scan_resume_candidates"]


@dataclass(frozen=True)
class BootReport:
    """Everything the boot gates learned, blocking or not."""

    skill_findings: tuple[Finding, ...]
    repo_findings: tuple[Finding, ...]
    threat_findings: tuple[Finding, ...]
    resume_candidates: tuple[TaskState, ...]
    corrupt_sidecars: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        """True when any blocking gate (skills, threat scan) fired."""
        return has_blocking(list(self.skill_findings)) or has_blocking(
            list(self.threat_findings)
        )

    def blocking_findings(self) -> tuple[Finding, ...]:
        return tuple(
            f
            for f in (*self.skill_findings, *self.threat_findings)
            if f.severity == "blocking"
        )


def run_boot_gates(*, working_dir: Path, paths: DreamPaths) -> BootReport:
    """Run every boot gate and return the combined report.

    Pure inspection — nothing here mutates the repo or starts work. The
    caller (``Runtime.start``) decides what blocking means (raise) and
    where warnings go (the event stream).
    """
    skill_findings = validate_skills(working_dir, home=paths.home)
    repo_findings = validate_repo(paths)
    threat_findings = threat_scan(paths)
    candidates, corrupt = _scan_sidecars(paths)
    return BootReport(
        skill_findings=tuple(skill_findings),
        repo_findings=tuple(repo_findings),
        threat_findings=tuple(threat_findings),
        resume_candidates=candidates,
        corrupt_sidecars=corrupt,
    )


def scan_resume_candidates(paths: DreamPaths) -> tuple[TaskState, ...]:
    """Sidecar states still marked ``running`` — work a dead process left."""
    candidates, _ = _scan_sidecars(paths)
    return candidates


def _scan_sidecars(
    paths: DreamPaths,
) -> tuple[tuple[TaskState, ...], tuple[str, ...]]:
    """Walk ``sidecars/`` once; split into resume candidates and corrupt dirs.

    A corrupt ``state.json`` must not abort boot — the runtime exists to
    keep running; the corrupt dir is reported so an operator (or a later
    GC pass) can act on it.
    """
    sidecars_dir = paths.sidecars_dir
    if not sidecars_dir.is_dir():
        return (), ()
    candidates: list[TaskState] = []
    corrupt: list[str] = []
    for entry in sorted(sidecars_dir.iterdir()):
        if not entry.is_dir() or not (entry / "state.json").exists():
            continue
        try:
            state = read_state(paths, entry.name)
        except (ValidationError, ValueError, OSError):
            corrupt.append(entry.name)
            continue
        if state.status == "running":
            candidates.append(state)
    return tuple(candidates), tuple(corrupt)
