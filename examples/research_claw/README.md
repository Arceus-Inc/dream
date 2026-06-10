# research_claw — a mini AutoResearchClaw on the dream runtime

Turn one research **idea** into a complete, **experimentally tested** paper.
A pared-down port of [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw)
(23 stages → 6) that keeps the part that matters: **the experiment really runs.**

## The pipeline

A deterministic orchestrator runs six stages in order; each is its own focused
session (fresh context). Between stage 3 and 4 the orchestrator executes the
generated code *itself* — the authoritative **oracle** run, captured to
`results.json` — so the analysis and paper stages reason over real measured
numbers, not the model's claim that the code ran.

| # | Stage | Artifact | Maps to AutoResearchClaw |
|---|---|---|---|
| 1 | scope | `problem.md` (problem + testable hypothesis + success metric) | Phase A (1-2) |
| 2 | related_work | `related_work.md` (via real `arxiv_search`) | Phase B (3-6) |
| 3 | experiment | `experiment.py` (writes code, runs it, iterates until green) | Phases C-D (7-11) |
| — | **oracle** | `results.json` (orchestrator runs it authoritatively) | Phase E (12-13) |
| 4 | analysis | `analysis.md` (interpret the real metrics) | Phase F (14-15) |
| 5 | paper | `paper.md` (full paper citing the measured numbers verbatim) | Phase G (16-19) |
| 6 | review | `review.md` (peer review checks number fidelity + honesty) | Phase H (20-23) |

What comes from dream: `build_harness`, the `SubprocessSandbox` (the real,
tree-killed executor — spec-15 P4), the sandbox-tier + trust-ramp governance,
and the session loop. `research_claw` adds the stages, the personas, the
`run_experiment` oracle tool, and `save_artifact`.

**Honesty by construction.** If the experiment never goes green, the run is not
aborted — `results.json` records the failure, `experiment_verified` is false,
and the paper stage is *required* to state this in Limitations and not claim
experimental support it doesn't have (AutoResearchClaw's proceed/pivot).

## Run it

```bash
export DREAM_API_KEY=sk-...        # any OpenAI-compatible endpoint
export DREAM_MODEL=gpt-4.1
./examples/research_claw/run.sh \
  "Nesterov momentum reaches a target loss in fewer iterations than plain GD on a convex quadratic" \
  ~/paper-lab
# or:
python examples/research_claw/agent.py --idea "..." --workspace ~/paper-lab
```

Keep ideas **small and CPU-cheap** — the experiment must run in seconds with
stdlib/numpy. Numerical-optimization, algorithmic, and statistical claims work
well; anything needing GPUs or large datasets does not (by design — this is the
mini version).

## What you get (a real run)

For the momentum idea above, the agent wrote a convex quadratic (κ=100),
measured **GD = 434 iterations** vs **Nesterov = 61** to reach 1e-6 relative loss
(7.1× faster, hypothesis supported), and the paper reported exactly those
numbers. The review stage independently re-checked every figure against
`results.json` and returned *Revise* (single-instance scope) — a genuine,
honest peer review, not a rubber stamp.

```
=== research_claw complete ===
stages run:   scope, related_work, experiment, analysis, paper, review
experiment:   VERIFIED (ran green with metrics)
paper:        ~/paper-lab/paper.md
```

## Tests

`uv run pytest tests/test_examples/test_research_claw.py` — metrics parsing,
the `run_experiment` oracle against the real sandbox (green / red / missing /
escape), artifact mechanics, and the full orchestrator (verified + unverified
paths) with injected fake stages. Offline.
