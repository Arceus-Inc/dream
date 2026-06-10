# research_claw — a cron-driven paper factory on the dream runtime

Drop research ideas into a queue; every N hours a **single autonomous
researcher agent** takes the top one all the way to a complete,
**experimentally tested** paper. Inspired by
[AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw), built
entirely on dream's public SDK.

No orchestrator: the agent owns its own workflow inside one session. The
harness does only what a harness should —

| harness responsibility | dream mechanism |
|---|---|
| fire every N hours, first fire **now**, survive restarts | cron manifest + `fire_now()` backdating |
| run each paper as a supervised, drained background task | runtime cron loop + `cron_argv_builder` → `--once` |
| give the agent its action space | `arxiv_search`, `write_file`/`read_file`, `run_experiment` (dream `SubprocessSandbox`), `save_artifact` — tier-gated + trust-promoted |
| **verify the deliverable** | after the session, the harness re-runs `experiment.py` itself (the oracle), records `results.json`, stamps VERIFIED / UNVERIFIED / NO-PAPER into `papers/INDEX.md` |

The agent is told the audit will happen ("invented numbers will be caught")
and that an experiment it can't make green must be disclosed in Limitations.

## Run it

```bash
export DREAM_API_KEY=sk-...        # any OpenAI-compatible endpoint
export DREAM_MODEL=gpt-4.1

./examples/research_claw/run.sh ~/paper-lab 6      # daemon: a paper every 6h
echo "your research idea" >> ~/paper-lab/ideas.md  # queue ideas any time
```

Each firing pops the top idea and produces:

```
papers/{stamp}-{slug}/
  problem.md  related_work.md  experiment.py  analysis.md  paper.md  review.md
  results.json        # the oracle's authoritative re-run
papers/INDEX.md       # one verdict line per paper
ideas_done.md         # the worked-off queue log
```

One paper right now, skipping the queue:

```bash
./examples/research_claw/run.sh --once "Nesterov momentum beats GD on a convex quadratic" ~/paper-lab
```

Watch it: `python -m dream.ctl --working-dir ~/paper-lab status|events`.
Keep ideas **small and CPU-cheap** (stdlib/numpy, seconds) — that's the mini in
mini-AutoResearchClaw.

## A real run

Queued *"Bubble sort's swap count on reversed input is exactly n(n-1)/2 —
verify empirically across sizes"*; the daemon's first cron fire popped it and
the agent — unprompted — tested both reversed inputs up to n=400 **and** the
stronger invariant (swap count = inversion count) on 200 random permutations:

```
metrics: {"reversed_tested": 400, "reversed_failures": 0,
          "random_trials": 200, "inv_mismatches": 0, "hypothesis_supported": true}
ideas_done.md: - [2026-06-10 17:10] VERIFIED: Bubble sort's swap count ... -> papers/...
review.md: Verdict: accept
```

The paper's Results section reports the oracle's metrics verbatim.

## Tests

`uv run pytest tests/test_examples/test_research_claw.py` — the experiment
tools against the real sandbox, the idea queue, bootstrap (cron manifest +
promotions), the cron argv payload, and `run_once` with an injected session
(verified / unverified / no-paper / crash / empty-queue paths). Offline.
