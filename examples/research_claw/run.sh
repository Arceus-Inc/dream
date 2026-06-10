#!/usr/bin/env bash
# One-command mini AutoResearchClaw: an idea -> an experimentally tested paper.
#
#   ./examples/research_claw/run.sh "your research idea" [workspace]
#
# Model credentials resolve like ohmo (env -> ./.env.local -> ~/Arceus/.env.local).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IDEA="${1:?usage: run.sh \"research idea\" [workspace]}"
WORKSPACE="${2:-$HOME/paper-lab}"

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
echo "research_claw: idea=\"$IDEA\" workspace=$WORKSPACE model=${DREAM_MODEL:-$DREAM_SMOKE_MODEL}"
cd "$REPO_ROOT"
exec uv run python examples/research_claw/agent.py --idea "$IDEA" --workspace "$WORKSPACE"
