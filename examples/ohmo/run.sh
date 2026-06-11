#!/usr/bin/env bash
# One-command ohmo launcher.
#
#   ./examples/ohmo/run.sh [workspace] [wake-idle-minutes]
#
# Credentials resolve in order:
#   1. DREAM_API_KEY / DREAM_MODEL / DREAM_BASE_URL already in the env
#   2. ./.env.local (DREAM_SMOKE_* contract, see .env.example)
#   3. ~/Arceus/.env.local (ARCEUS_AZURE_OPENAI_* mapped to DREAM_*)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKSPACE="${1:-$HOME/ohmo-lab}"
WAKE_MINUTES="${2:-5}"

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
  echo "no credentials: set DREAM_API_KEY/DREAM_MODEL or provide .env.local" >&2
  exit 2
fi

echo "ohmo: workspace=$WORKSPACE wake-every=${WAKE_MINUTES}m model=${DREAM_MODEL:-$DREAM_SMOKE_MODEL}"
echo "steer it:  uv run python -m dream.ctl --working-dir $WORKSPACE status|wake|events"
cd "$REPO_ROOT"
exec uv run python examples/ohmo/agent.py \
  --workspace "$WORKSPACE" \
  --wake-idle-minutes "$WAKE_MINUTES"
