"""Spec 12e — file_verification_tech_debt: failures → #07 tracker bullets."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dream.tasks._tech_debt import tech_debt_path
from dream.verification._tech_debt import (
    file_verification_tech_debt,
    parse_matchers,
)
from dream.verification._types import RepoVerificationStep, VerificationReport

_NOW = datetime(2026, 6, 8, tzinfo=UTC)
_MATCHERS = parse_matchers(
    "[[matcher]]\npattern = \"No module named '([\\\\w.]+)'\"\nmissing = \"module: {1}\"\n"
)


def _failed(stderr: str, *, name: str = "", command: str = "pytest") -> RepoVerificationStep:
    return RepoVerificationStep(
        command=command, status="failed", returncode=1, stderr=stderr, name=name
    )


def _file(report: VerificationReport, root: Path):
    return file_verification_tech_debt(
        report,
        task_id="T1",
        tracker_root=root,
        report_path="docs/.../verification-report.json",
        matchers=_MATCHERS,
        now=_NOW,
    )


def test_matched_failure_is_filed(tmp_path: Path) -> None:
    report = VerificationReport(steps=(_failed("No module named 'httpx'"),))
    result = _file(report, tmp_path)

    assert len(result.filed) == 1
    assert result.unmatched == ()
    entry = result.filed[0]
    assert entry.missing == "module: httpx"
    assert entry.source == "verification.failure"
    assert entry.task_id == "T1"
    assert entry.ts == _NOW
    # actually appended to the tracker file
    assert "module: httpx" in tech_debt_path(tmp_path).read_text(encoding="utf-8")


def test_unmatched_failure_files_nothing(tmp_path: Path) -> None:
    report = VerificationReport(steps=(_failed("opaque stack trace"),))
    result = _file(report, tmp_path)
    assert result.filed == ()
    assert len(result.unmatched) == 1
    assert not tech_debt_path(tmp_path).exists()  # nothing written


def test_mixed_report_files_only_matched(tmp_path: Path) -> None:
    report = VerificationReport(
        steps=(
            _failed("No module named 'pydantic'", name="unit"),
            _failed("some other error", name="lint"),
        )
    )
    result = _file(report, tmp_path)
    assert len(result.filed) == 1 and len(result.unmatched) == 1
    assert result.filed[0].missing == "module: pydantic"


def test_success_report_files_nothing(tmp_path: Path) -> None:
    report = VerificationReport(
        steps=(RepoVerificationStep(command="echo ok", status="success", returncode=0),)
    )
    result = _file(report, tmp_path)
    assert result.filed == () and result.unmatched == ()


def test_evidence_points_at_report_and_step(tmp_path: Path) -> None:
    report = VerificationReport(steps=(_failed("No module named 'x'", name="unit-tests"),))
    entry = _file(report, tmp_path).filed[0]
    assert "verification-report.json" in entry.evidence
    assert "unit-tests" in entry.evidence


def test_error_status_also_filed(tmp_path: Path) -> None:
    step = RepoVerificationStep(
        command="pytest", status="error", returncode=None, stderr="No module named 'y'"
    )
    result = _file(VerificationReport(steps=(step,)), tmp_path)
    assert len(result.filed) == 1
