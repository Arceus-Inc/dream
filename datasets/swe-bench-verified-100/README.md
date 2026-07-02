# swe-bench-verified-100 — 100 real long-horizon coding tasks

100 tasks curated from **SWE-bench Verified** (`princeton-nlp/SWE-bench_Verified`,
500 human-validated real GitHub issues). Each task is a real bug/feature from a
major Python project, shipped with the **gold fix** and a **test oracle** — so you
have *task + result + grading signal* in one record.

Source: [SWE-bench Verified on Hugging Face](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified) · benchmark by Princeton NLP / OpenAI. This file stores only task **metadata** (issue text, gold patch, test ids); the repo source is cloned at eval time from the public GitHub repos.

## Selection

Curated to be **hard, not trivial** (the "<15 min fix" tier is excluded entirely)
and **diverse across repositories** (per-repo cap so django doesn't dominate):

- **By difficulty:** `1-4 hours` ×42, `>4 hours` ×3, `15 min - 1 hour` ×55  → 45 hard/medium-hard + 55 medium, **0 trivial**.
- **By repo:** django 22, astropy 10, xarray 10, pytest 10, scikit-learn 10, sphinx 9, sympy 9, matplotlib 9, pylint 7, seaborn 2, requests 2 (11 repos).
- Avg problem-statement length ≈ 2,960 chars; tasks include real multi-file refactors with hundreds of regression tests.

## Format (`tasks.jsonl`, one JSON object per line)

| field | meaning |
|---|---|
| `instance_id` | `owner__repo-PR#` (unique id) |
| `repo`, `base_commit` | clone `repo` @ `base_commit` to reproduce the broken state |
| `environment_setup_commit`, `version` | for building the project's test env |
| `difficulty` | SWE-bench Verified human label |
| **`problem_statement`** | **the task** — the GitHub issue text given to the agent |
| `hints_text` | extra discussion from the issue thread (optional context) |
| **`gold_patch`** | **the result** — the reference diff that actually fixed it |
| `test_patch` | the diff that adds the regression tests |
| **`FAIL_TO_PASS`** | **oracle** — tests that must flip from fail→pass after the fix |
| **`PASS_TO_PASS`** | **oracle** — tests that must stay green (no regressions) |

## How to grade dream's `run_task` on a task

```
1. git clone <repo>; git checkout <base_commit>     # broken state, gold_patch withheld
2. (build the project's test env at environment_setup_commit/version)
3. run_task(intent = problem_statement, worktree_root = clone,
            verification_steps = [run FAIL_TO_PASS], max_sprints = N)
4. apply test_patch; run FAIL_TO_PASS + PASS_TO_PASS
   PASS  ⇔  all FAIL_TO_PASS now pass AND all PASS_TO_PASS still pass
```

`gold_patch` is the upper bound (always green). For a *canonical* score, emit
`{"instance_id", "model_patch": <agent diff>}` JSONL and grade with the official
[SWE-bench Docker harness](https://github.com/princeton-nlp/SWE-bench); for fast
iteration, run the two test sets directly in the worktree.

## Regenerate / re-curate

`build script` (ad-hoc): download the parquet from the HF `refs/convert/parquet`
branch and re-run the stratified selection (exclude `<15 min fix`, cap per repo).
See the session that produced this file, or re-pull from the source dataset above.

## Notes & caveats

- **Heavy to run:** these need each project's full test env (Docker recommended) — unlike a self-contained unit test. Budget compute accordingly.
- **Contamination:** Verified overlaps model training data — fine for harness/iteration testing, not for a headline capability claim (use SWE-bench **Pro** / a private mined set for that).
- License: SWE-bench is research-licensed; cloned repo code carries each project's own license.
