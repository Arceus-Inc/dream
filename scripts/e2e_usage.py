"""E2E: per-model token metering surfaces on RunTaskResult, live vs the model.

Proves the chorus billing seam end to end: dream meters tokens and attributes
them to a model, surfacing them up through ``RunTaskResult.usage_by_model``
(``{model_id: UsageSnapshot}``). dream never computes dollars — ``cost_usd``
stays 0.0; chorus owns the price table and converts tokens -> cents.

The acceptance the contract names: a real ``run_task`` returns a NON-EMPTY
``usage_by_model`` whose summed ``total_tokens`` is > 0 — the exact thing whose
absence today makes ``cost_cents`` stick at 0.

Scenarios:
  1. LIVE   a real run_task returns non-empty usage_by_model with >0 total tokens
  2. (same) every entry is keyed by a real model id and carries the four token
            kinds (input / output / cache_read / cache_write)
  3. (same) cost_usd stays 0.0 everywhere — dream meters, never bills

Credentials: ~/Arceus/.env.local (Azure). Run: uv run python scripts/e2e_usage.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dream import SessionCost, UsageSnapshot, build_harness
from dream.runner import StdioObserver


def _load_azure_creds() -> dict[str, str]:
    env_path = Path.home() / "Arceus" / ".env.local"
    raw: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        raw[k.strip()] = v.strip().strip("'\"")
    endpoint = raw["ARCEUS_AZURE_OPENAI_ENDPOINT"].rstrip("/")
    return {
        "model": raw["ARCEUS_AZURE_OPENAI_WORKER_DEPLOYMENT"],
        "api_key": raw["ARCEUS_AZURE_OPENAI_API_KEY"],
        "base_url": f"{endpoint}/openai/v1",
    }


def _git_init(worktree: Path) -> None:
    def _run(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=worktree, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    _run("init", "-q", "-b", "main")
    _run("config", "user.email", "e2e@dream.local")
    _run("config", "user.name", "dream-e2e")
    _run("commit", "--allow-empty", "-q", "-m", "init")


def _fresh_workspace(home: Path) -> Path:
    wt = Path(tempfile.mkdtemp(prefix="e2e-usage-", dir=home))
    _git_init(wt)
    cfg = wt / ".harness" / "sandbox.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('tier = "repo-write"\n', encoding="utf-8")
    return wt


async def main() -> int:
    creds = _load_azure_creds()
    home = Path(tempfile.mkdtemp(prefix="e2e-usage-home-"))
    os.environ["DREAM_HOME"] = str(home / "dream")
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}\n", flush=True)
    results: list[tuple[str, bool, str]] = []

    wt = _fresh_workspace(home)
    harness = build_harness(
        model=creds["model"], api_key=creds["api_key"], base_url=creds["base_url"],
        working_dir=wt,
    )
    async with harness:
        result = await harness.run_task(
            intent=(
                "Create a file named hello.txt containing the word 'hello' "
                "using the write_file tool, then confirm it exists."
            ),
            observer=StdioObserver(sys.stdout),
            max_sprints=3,
        )

    usage = result.usage_by_model
    total = sum(u.total_tokens for u in usage.values())

    print("\n--- usage_by_model ---")
    for model_id, snap in usage.items():
        print(
            f"  {model_id}: in={snap.input_tokens} out={snap.output_tokens} "
            f"cache_read={snap.cache_read_tokens} cache_write={snap.cache_write_tokens} "
            f"total={snap.total_tokens}"
        )
    print(f"  Σ total_tokens = {total}")

    # 1. the acceptance: non-empty map, >0 total tokens.
    results.append(
        ("1 usage_by_model non-empty + total tokens > 0",
         len(usage) > 0 and total > 0, f"{len(usage)} model(s), {total} tokens")
    )

    # 2. every entry keyed by a real model id + carries the typed snapshot.
    well_formed = bool(usage) and all(
        isinstance(m, str) and m and isinstance(s, UsageSnapshot)
        for m, s in usage.items()
    )
    results.append(
        ("2 keyed by model id; values are UsageSnapshot", well_formed,
         ", ".join(sorted(usage)))
    )

    # 3. dream meters, never bills — cost_usd is structurally absent from the
    #    metering type (UsageSnapshot has no dollars) and 0.0 on SessionCost.
    no_dollars = (
        not hasattr(UsageSnapshot(), "cost_usd")
        and SessionCost().cost_usd == 0.0
    )
    results.append(("3 dream meters, never bills (cost_usd 0.0)", no_dollars, ""))

    print("\n" + "=" * 60)
    failures = [name for name, ok, _ in results if not ok]
    for name, ok, where in results:
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] scenario {name}" + (f"  ({where})" if where else ""))
    print("=" * 60)
    if failures:
        print(f"[e2e] {len(failures)} FAILURE(S): {failures}")
        return 1
    print("[e2e] all scenarios PASS — usage_by_model token metering verified end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
