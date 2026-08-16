"""Cross-repo eval: dream + chorus OTEL gates and hierarchy contracts.

Assumes both worktrees are on ``feat/otel-architecture`` with OTel installed.
Exit 0 iff all checks pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DREAM = Path(os.environ.get("DREAM_REPO_ROOT", REPO_ROOT))
CHORUS = Path(os.environ.get("CHORUS_REPO_ROOT", REPO_ROOT.parent / "chorus"))


def _run(cwd: Path, script: Path) -> None:
    env = os.environ.copy()
    env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    print(f"--- {cwd.name}/{script.name} (exit {proc.returncode}) ---")
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> None:
    for name, root, script in (
        ("DREAM_REPO_ROOT", DREAM, DREAM / "evals/otel/eval_step1_foundation.py"),
        ("CHORUS_REPO_ROOT", CHORUS, CHORUS / "evals/otel/eval_step1_chorus_otlp.py"),
    ):
        if not root.is_dir():
            raise SystemExit(f"{name} does not exist or is not a directory: {root}")
        if not script.is_file():
            raise SystemExit(f"{name} is missing the eval script: {script}")
    _run(DREAM, DREAM / "evals/otel/eval_step1_foundation.py")
    _run(CHORUS, CHORUS / "evals/otel/eval_step1_chorus_otlp.py")
    print("eval_cross_repo: all checks passed")


if __name__ == "__main__":
    main()
