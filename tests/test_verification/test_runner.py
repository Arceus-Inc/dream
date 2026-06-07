"""Spec 12c — verification runner: run shell steps, map status, UI seam, report."""

from __future__ import annotations

import json
from pathlib import Path

from dream.verification._report import write_report
from dream.verification._runner import run_verification
from dream.verification._types import RepoVerificationStep, VerificationStepSpec
from dream.verification._ui import SkipUiVerifier


def _spec(command: str, name: str = "") -> VerificationStepSpec:
    return VerificationStepSpec(command=command, name=name)


async def test_successful_step(tmp_path: Path) -> None:
    report = await run_verification([_spec("echo hello")], cwd=tmp_path)
    step = report.steps[0]
    assert step.status == "success"
    assert step.returncode == 0
    assert "hello" in step.stdout
    assert report.status == "success"


async def test_failed_step_nonzero_exit(tmp_path: Path) -> None:
    report = await run_verification([_spec("exit 3")], cwd=tmp_path)
    assert report.steps[0].status == "failed"
    assert report.steps[0].returncode == 3
    assert report.status == "failed"


async def test_timeout_is_error(tmp_path: Path) -> None:
    report = await run_verification(
        [_spec("sleep 5")], cwd=tmp_path, timeout_seconds=0.2
    )
    step = report.steps[0]
    assert step.status == "error"
    assert step.returncode is None
    assert "tim" in step.stderr.lower()


async def test_multiple_steps_overall_status_is_worst(tmp_path: Path) -> None:
    report = await run_verification(
        [_spec("echo ok"), _spec("exit 1")], cwd=tmp_path
    )
    assert [s.status for s in report.steps] == ["success", "failed"]
    assert report.status == "failed"
    assert len(report.failures) == 1


async def test_ui_paths_skipped_by_default(tmp_path: Path) -> None:
    report = await run_verification([], cwd=tmp_path, ui_paths=("/dashboard",))
    assert len(report.steps) == 1
    assert report.steps[0].status == "skipped"
    assert report.status == "success"  # skipped is not a failure


async def test_ui_verifier_seam_is_used(tmp_path: Path) -> None:
    class _OkUi:
        async def verify(self, user_path: str) -> RepoVerificationStep:
            return RepoVerificationStep(
                command=f"ui:{user_path}", status="success", name=user_path
            )

    report = await run_verification(
        [], cwd=tmp_path, ui_paths=("/home",), ui_verifier=_OkUi()
    )
    assert report.steps[0].status == "success"
    assert report.steps[0].name == "/home"


def test_skip_ui_verifier_returns_skipped() -> None:
    import asyncio

    step = asyncio.run(SkipUiVerifier().verify("/x"))
    assert step.status == "skipped" and step.name == "/x"


# --- report -----------------------------------------------------------------


async def test_write_report_roundtrips(tmp_path: Path) -> None:
    report = await run_verification([_spec("echo hi", name="smoke")], cwd=tmp_path)
    path = tmp_path / "metrics" / "verification-report.json"
    write_report(report, path, scratch_dir=tmp_path / "scratch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["steps"][0]["name"] == "smoke"
    assert payload["steps"][0]["status"] == "success"


async def test_write_report_offloads_large_output(tmp_path: Path) -> None:
    huge = "x" * 200_000
    report = await run_verification([_spec(f"printf '{huge}'")], cwd=tmp_path)
    path = tmp_path / "report.json"
    write_report(report, path, scratch_dir=tmp_path / "scratch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    step = payload["steps"][0]
    # The inline stdout is truncated and an offload ref points to the full output.
    assert len(step["stdout"]) < len(huge)
    assert step["stdout_offloaded_to"]
