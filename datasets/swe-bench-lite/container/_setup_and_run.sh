#!/usr/bin/env bash
# Thin launcher (WSL): ensure an isolated swebench venv exists, then run the
# container orchestrator. The model key comes from the environment, never this file.
#   BENCH_MODEL_API_KEY=… bash _setup_and_run.sh --harness dream --only <id> …
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
VENV="$HOME/.sweb/venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "=== creating swebench venv ==="
  uv venv "$VENV" --python 3.12
  uv pip install --python "$VENV/bin/python" swebench >/dev/null
  echo "swebench installed"
fi
exec "$VENV/bin/python" /mnt/q/projects/inspired-arc/dream/datasets/swe-bench-lite/container/run_container.py "$@"
