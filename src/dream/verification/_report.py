"""Persist a verification report to JSON, offloading large step output (Spec 12c).

A failing step's stdout/stderr can be enormous; each is routed through the #04
offload contract so the report stays small — inline text is truncated and an
``*_offloaded_to`` pointer records where the full payload lives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dream.services.tool_outputs import offload_tool_output
from dream.utils.fs import save_json_file
from dream.verification._types import RepoVerificationStep, VerificationReport


def write_report(report: VerificationReport, path: str | Path, *, scratch_dir: Path) -> None:
    """Write the report JSON to ``path``, offloading oversized step output."""
    payload = {
        "status": report.status,
        "steps": [_serialise_step(step, scratch_dir, index) for index, step in enumerate(report.steps)],
    }
    save_json_file(path, payload, trailing_newline=False)


def _serialise_step(
    step: RepoVerificationStep, scratch_dir: Path, index: int
) -> dict[str, Any]:
    # Returned shape (one element of the report's "steps" list):
    #   {"command": str, "name": str, "status": str, "returncode": int,
    #    "stdout": str, "stderr": str,
    #    "stdout_offloaded_to"?: str, "stderr_offloaded_to"?: str}
    # The ``*_offloaded_to`` keys are present only when that stream was
    # offloaded (oversized) per the #04 offload contract.
    stdout, stdout_ptr = offload_tool_output(
        step.stdout, scratch_dir=scratch_dir, tool_use_id=f"verif-{index}-out", tool_name="verification"
    )
    stderr, stderr_ptr = offload_tool_output(
        step.stderr, scratch_dir=scratch_dir, tool_use_id=f"verif-{index}-err", tool_name="verification"
    )
    data: dict[str, Any] = {
        "command": step.command,
        "name": step.name,
        "status": step.status,
        "returncode": step.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if stdout_ptr is not None:
        data["stdout_offloaded_to"] = stdout_ptr.offloaded_to
    if stderr_ptr is not None:
        data["stderr_offloaded_to"] = stderr_ptr.offloaded_to
    return data


__all__ = ["write_report"]
