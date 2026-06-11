#!/usr/bin/env bash
# One-command paper factory.
#
#   ./examples/research_claw/run.sh [workspace] [every-hours]   # daemon
#   ./examples/research_claw/run.sh --once "idea" [workspace]   # one paper now
#
# Daemon mode: drop ideas into <workspace>/ideas.md (one per line); every N
# hours (first fire immediately) cron pops the top idea and a researcher
# session produces papers/{stamp}-{slug}/paper.md, audited by the oracle.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MODE="daemon"; IDEA=""
if [[ "${1:-}" == "--once" ]]; then
  MODE="once"; IDEA="${2:?usage: run.sh --once \"idea\" [workspace]}"
  WORKSPACE="${3:-$HOME/paper-lab}"
else
  WORKSPACE="${1:-$HOME/paper-lab}"
  EVERY="${2:-6}"
fi

if [[ -z "${DREAM_API_KEY:-}" && -f "$REPO_ROOT/.env.local" ]]; then
  set -a; source "$REPO_ROOT/.env.local"; set +a
fi
if [[ -z "${DREAM_API_KEY:-}${DREAM_SMOKE_API_KEY:-}" && -f "$HOME/Arceus/.env.local" ]]; then
  set -a; source "$HOME/Arceus/.env.local"; set +a
  export DREAM_API_KEY="$ARCEUS_AZURE_OPENAI_API_KEY"
  export DREAM_BASE_URL="${ARCEUS_AZURE_OPENAI_ENDPOINT%/}/openai/v1"
  export DREAM_MODEL="${ARCEUS_AZURE_OPENAI_WORKER_DEPLOYMENT}"
fi
if [[ -z "${DREAM_API_KEY:-}${DREAM_SMOKE_API_KEY:-}" ]]; then
  echo "no credentials: set DREAM_API_KEY/DREAM_MODEL or provide .env.local" >&2; exit 2
fi

cd "$REPO_ROOT"
if [[ "$MODE" == "once" ]]; then
  echo "research_claw --once: \"$IDEA\" -> $WORKSPACE/papers/"
  exec uv run python examples/research_claw/agent.py --once --idea "$IDEA" --workspace "$WORKSPACE"
fi
echo "research_claw daemon: workspace=$WORKSPACE every=${EVERY}h (first fire now)"
echo "queue ideas:  echo 'your idea' >> $WORKSPACE/ideas.md"
exec uv run python examples/research_claw/agent.py --workspace "$WORKSPACE" --every-hours "$EVERY"
