"""Cross-repo eval: dream + chorus OTEL gates and hierarchy contracts.

Assumes both worktrees are on ``feat/otel-architecture`` with ``[otel]`` installed.
Exit 0 iff all checks pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DREAM = Path("/Users/divyansh/dream-wt-otel")
CHORUS = Path("/Users/divyansh/chorus-wt-otel")


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
    _run(DREAM, DREAM / "evals/otel/eval_step1_foundation.py")
    _run(CHORUS, CHORUS / "evals/otel/eval_step1_chorus_otlp.py")
    print("eval_cross_repo: all checks passed")


if __name__ == "__main__":
    main()
