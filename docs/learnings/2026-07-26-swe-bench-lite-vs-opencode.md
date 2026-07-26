# SWE-bench Lite: `dream` vs `opencode`

**Date:** 2026-07-26 · **Tasks:** 25 (SWE-bench Lite, stratified across 10 repos) ·
**Model:** `gpt-5.2` (identical endpoint, identical prompt body for both harnesses) ·
**Grading:** official SWE-bench Docker harness (`swebench` 4.1.0)

Harness under test: `dream` at [`f0a0f73`](https://github.com/Arceus-Inc/dream/commit/f0a0f73),
driven **only** through its public API — `build_default_harness()` + `Harness.run_task()`.
Baseline: [opencode](https://github.com/anomalyco/opencode) v1.18.5.

Everything below is reproducible from [`datasets/swe-bench-lite/`](../../datasets/swe-bench-lite/).

---

## Headline

| | dream | opencode |
|---|---|---|
| **resolved** | **19/25 (76%)** | **21/25 (84%)** |
| total wall time | 86.8 min | 84.1 min |
| total tokens | 12.85 M | 10.79 M |
| median time / task | 122 s | 98 s |
| median tokens / task | 288 k | 108 k |
| median agent steps | 1 sprint (max 2) | 10 steps (max 48) |
| median patch size | 997 B | 1141 B |
| empty patches | 1 | 0 |
| harness errors | 0 | 0 |

A 2-task gap at n=25 is inside noise: the two harnesses are **effectively even on
capability**. What differs — and what is actually actionable — is *how they fail*.

Divergent tasks (6 of 25):

- **dream only:** `pylint-dev__pylint-7114`, `sphinx-doc__sphinx-8506`
- **opencode only:** `django__django-16820`, `pydata__xarray-4248`, `sympy__sympy-15346`, `sympy__sympy-19254`
- **neither:** `psf__requests-2317`, `psf__requests-3362`

---

## What we learned

### 1. The evaluate gate is only as good as the evidence it can collect

The first pass ran the agent against the repo at `base_commit`. On SWE-bench, the
`FAIL_TO_PASS` tests are *added* by the task's `test_patch` — at `base_commit` they do not
exist. `dream`'s evaluator correctly refused to certify ("no oracle evidence that the tests
pass") and the runner spent every one of its sprints trying again.

On `pallets__flask-4045` that was **499 k tokens and a `fail`**. With the oracle test file
present, the same task became **258 k tokens and a `pass` in one sprint** — with a
byte-identical fix. The model was never the problem.

> **Conclusion:** when verification cannot run, the plan → sprint → evaluate loop degrades
> *badly*, not gracefully — it converts an unverifiable task into maximum spend. A harness
> that gates on evidence needs an explicit "verification is unavailable" state that is
> distinct from "verification failed", and it must stop rather than retry.

### 2. Failure economics are inverted, and that is the real finding

|  | dream | opencode |
|---|---|---|
| resolved → mean time / tokens | 154 s / 388 k | 179 s / 408 k |
| **unresolved → mean time / tokens** | **380 s / 912 k** | **320 s / 554 k** |

`dream`'s failures cost ~2.4× its successes. The worst case, `django__django-16820`,
burned **2.78 M tokens over 499 s** and still did not resolve — more than any successful
task by a wide margin. `opencode`'s median failure is *cheap* (87 k tokens): it gives up.

`dream` has no cheap-abort signal. It will keep spending as long as sprints remain.
Confidence that a trajectory is wrong is information the loop currently discards.

### 3. Sprint granularity trades correction opportunities for context

`dream` ran a median of **1 sprint** (max 2); `opencode` a median of **10 steps** (max 48).
Fewer, larger units of work means fewer moments where a wrong assumption can be caught. On
the four tasks `dream` lost but `opencode` won, `dream` committed to one long trajectory;
`opencode` self-corrected mid-flight.

This is a deliberate design position, not an accident — but the benchmark prices it.

### 4. Reporting success with an empty diff is a correctness bug

On `sympy__sympy-15346`, `run_task` returned **ok after 447 s and 192 k tokens** while
producing a **0-byte diff**. The evaluate gate certified a run that changed nothing. Whatever
the cause, "passed" and "produced no change" must never be simultaneously true.

*Tracked as a follow-up; this is the single clearest bug the benchmark surfaced.*

### 5. The SDK boundary held

Benchmarking required **zero changes to `src/dream/`**. The whole runner is
`build_default_harness()` + `run_task(intent=…, worktree_root=…, verification_steps=…,
max_sprints=…)`, plus reading `result.usage_by_model`. No private imports, no monkeypatching,
no wrapper "agent logic" compensating for missing harness features — which was the explicit
bar for this comparison being fair.

That is the strongest positive result here: the public API is sufficient to drive a real
autonomous coding workload end to end.

---

## Setup, precisely

Both harnesses ran **inside the task's official SWE-bench evaluation container**, so the
agent had the real, built test environment — not a synthetic checkout.

1. Pull `swebench/sweb.eval.x86_64.<instance>`; `/testbed` holds the repo at `base_commit`.
2. Apply and commit the oracle `test_patch` **before** the agent starts, so the acceptance
   tests exist and the agent can actually run them.
3. Run the agent against `/testbed`.
4. `model_patch` = `git diff` against the post-oracle commit, **excluding test files and
   harness scaffolding**, so the agent cannot score by editing the tests.
5. Grade with the official harness, which re-applies the pristine `test_patch` — so step 2
   cannot inflate the result.

Agent configuration:

- **dream** — `run_task(verification_steps=({"kind": "test", "command": "<testbed python> -m
  pytest -q <FAIL_TO_PASS>"},), max_sprints=5)`, sandbox tier `unrestricted`, 1500 s cap.
- **opencode** — `opencode run --dir /testbed --model azure/gpt-5.2 --format json --auto
  "<same prompt>"`, 1500 s cap.

### Caveats that must travel with these numbers

- **This benchmark is oracle-assisted by design.** Both agents are told the acceptance test
  command. That is why resolve rates are far above published SWE-bench Lite leaderboards;
  these numbers are **not** comparable to them. The comparison between the two harnesses is
  the only valid reading.
- **Token meters are not identical.** `dream` reports `input + output`, with cached context
  folded into `input`. `opencode`'s per-step `total` includes cache reads and reasoning
  tokens — its *non-cached* input median is only 17.8 k. Treat token totals as indicative of
  magnitude, not as a like-for-like billing comparison.
- **One residual asymmetry.** `dream` receives the acceptance command through its native
  `verification_steps` parameter; `opencode` receives it only in the prose prompt. Both know
  the command, but `dream`'s loop can act on it structurally. A strict prompt-only rerun is
  the honest follow-up.
- n=25, single run, no seed control. Differences of 1–2 tasks are noise.

## Reproducing

Requires Linux (or WSL) with Docker — the `swebench` package imports the Unix-only
`resource` module and each task pulls a ~1.5 GB image.

```bash
cd datasets/swe-bench-lite
BENCH_MODEL_API_KEY=… bash container/_setup_and_run.sh --harness dream --max-sprints 5
BENCH_MODEL_API_KEY=… bash container/_setup_and_run.sh --harness opencode
python grade.py   --preds results/dream/predictions.jsonl --run-id dreamFULL
python stats.py                        # per-task table + distributions
python compare.py --run-id full        # COMPARISON.md + comparison.json
```

## Follow-ups this opened

1. Fix the empty-diff-but-passing path (§4) — correctness.
2. Give the runner a cheap abort: distinguish "verification unavailable" from "verification
   failed", and stop instead of consuming the sprint budget (§1, §2).
3. Rerun with prompt-only parity to close the last asymmetry (caveats).
4. Investigate `psf__requests-2317`, where *both* harnesses burned 15–17 minutes and failed —
   likely a task where the loop cannot tell it is stuck.
