#!/usr/bin/env bash
# One-command rolling-digest launcher.
#
#   ./examples/digest/run.sh [workspace]            # daemon: every 2h, from now
#   ./examples/digest/run.sh --once [workspace]     # one digest now, then exit
#
# Each run writes research_ideas/{timestamp}.md covering the last 2 hours of
# self-evolution AI news. No email. Model credentials resolve like ohmo's
# launcher (env -> ./.env.local -> ~/Arceus/.env.local).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ONCE=""
if [[ "${1:-}" == "--once" ]]; then
  ONCE="--once"; shift
fi
WORKSPACE="${1:-$HOME/digest-lab}"

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

echo "digest: workspace=$WORKSPACE mode=${ONCE:-daemon} every=2h model=${DREAM_MODEL:-$DREAM_SMOKE_MODEL}"
echo "output: $WORKSPACE/research_ideas/{timestamp}.md"
cd "$REPO_ROOT"
exec uv run python examples/digest/agent.py $ONCE --workspace "$WORKSPACE"
