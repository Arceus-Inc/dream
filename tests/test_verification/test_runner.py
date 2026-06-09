"""Spec 12c — verification runner: run shell steps, map status, UI seam, report."""

from __future__ import annotations

import asyncio
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


async def test_ui_verifier_exception_becomes_error_step(tmp_path: Path) -> None:
    # A crashing UI verifier must produce an ``error`` step, not abort the run.
    class _BoomUi:
        async def verify(self, user_path: str) -> RepoVerificationStep:
            raise RuntimeError("driver crashed")

    report = await run_verification(
        [_spec("echo ok")], cwd=tmp_path, ui_paths=("/home",), ui_verifier=_BoomUi()
    )
    assert [s.status for s in report.steps] == ["success", "error"]
    assert "driver crashed" in report.steps[1].stderr
    assert report.steps[1].name == "/home"


async def test_falsy_ui_verifier_is_honoured(tmp_path: Path) -> None:
    # A verifier that is falsy (``__bool__`` False) must still be used — an
    # ``or`` would silently swap it for the skip default.
    class _FalsyUi:
        def __bool__(self) -> bool:
            return False

        async def verify(self, user_path: str) -> RepoVerificationStep:
            return RepoVerificationStep(
                command=f"ui:{user_path}", status="success", name=user_path
            )

    report = await run_verification(
        [], cwd=tmp_path, ui_paths=("/home",), ui_verifier=_FalsyUi()
    )
    assert report.steps[0].status == "success"  # not "skipped"


async def test_timeout_kills_child_process_group(tmp_path: Path) -> None:
    # A timed-out step spawns a background child; killing the group must reap it.
    marker = tmp_path / "alive.txt"
    # Parent sleeps briefly; a backgrounded grandchild would outlive a PID-only
    # kill and create the marker. A group kill prevents the marker entirely.
    command = f"(sleep 3; touch {marker}) & sleep 5"
    report = await run_verification([_spec(command)], cwd=tmp_path, timeout_seconds=0.3)
    assert report.steps[0].status == "error"
    await asyncio.sleep(1.0)  # give a leaked child time to fire its touch
    assert not marker.exists()


def test_skip_ui_verifier_returns_skipped() -> None:
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
    # Produce ~200 KB of output from a SHORT command: embedding the payload in
    # the command string exceeds Linux's per-argument limit (MAX_ARG_STRLEN,
    # 128 KB), so the subprocess fails to spawn there (it works on macOS).
    report = await run_verification([_spec("yes x | head -n 100000")], cwd=tmp_path)
    path = tmp_path / "report.json"
    write_report(report, path, scratch_dir=tmp_path / "scratch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    step = payload["steps"][0]
    # The inline stdout is truncated and an offload ref points to the full output.
    assert len(step["stdout"]) < 200_000
    assert step["stdout_offloaded_to"]
