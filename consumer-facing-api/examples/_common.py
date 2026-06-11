"""Shared plumbing for the consumer-facing examples.

Credentials come from three environment variables so every example runs the
same way (any OpenAI-compatible endpoint):

    export DREAM_MODEL=...      # model / deployment name
    export DREAM_API_KEY=...
    export DREAM_BASE_URL=...   # e.g. https://api.openai.com/v1

Optionally point DREAM_ENV_FILE at a KEY=VALUE file to load them from disk.
Each example also needs a git workspace; ``fresh_workspace`` makes a throwaway
one under /tmp so the examples never touch your real repos.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

REQUIRED = ("DREAM_MODEL", "DREAM_API_KEY", "DREAM_BASE_URL")


def load_creds() -> dict[str, str]:
    """Return model/api_key/base_url from the environment (or DREAM_ENV_FILE)."""
    env_file = os.environ.get("DREAM_ENV_FILE")
    if env_file:
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            f"missing env: {', '.join(missing)} — see consumer-facing-api/QUICKSTART.md"
        )
    return {
        "model": os.environ["DREAM_MODEL"],
        "api_key": os.environ["DREAM_API_KEY"],
        "base_url": os.environ["DREAM_BASE_URL"],
    }


def fresh_workspace(prefix: str = "dream-example-", tier: str = "repo-write") -> Path:
    """Create a throwaway git workspace with a sandbox tier set."""
    workspace = Path(tempfile.mkdtemp(prefix=prefix))

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=workspace, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "example@dream.local")
    git("config", "user.name", "dream-example")
    git("commit", "--allow-empty", "-q", "-m", "init")
    sandbox = workspace / ".harness" / "sandbox.toml"
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    sandbox.write_text(f'tier = "{tier}"\n', encoding="utf-8")
    return workspace
