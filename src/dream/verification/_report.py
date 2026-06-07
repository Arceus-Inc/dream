"""Persist a verification report to JSON, offloading large step output (Spec 12c).

A failing step's stdout/stderr can be enormous; each is routed through the #04
offload contract so the report stays small — inline text is truncated and an
``*_offloaded_to`` pointer records where the full payload lives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dream.services.tool_outputs import offload_tool_output
from dream.utils.fs import atomic_write_text
from dream.verification._types import RepoVerificationStep, VerificationReport


def write_report(report: VerificationReport, path: str | Path, *, scratch_dir: Path) -> None:
    """Write the report JSON to ``path``, offloading oversized step output."""
    payload = {
        "status": report.status,
        "steps": [_serialise_step(step, scratch_dir, index) for index, step in enumerate(report.steps)],
    }
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=False))


def _serialise_step(
    step: RepoVerificationStep, scratch_dir: Path, index: int
) -> dict[str, Any]:
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
