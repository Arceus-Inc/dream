# SWE-bench Lite — harness comparison rig

Runs an agent harness against a 25-task subset of
[SWE-bench Lite](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite) **inside each
task's official evaluation container**, then grades the resulting patches with the official
SWE-bench Docker harness.

Two harnesses are wired up — `dream` and [opencode](https://github.com/anomalyco/opencode) —
so the same model, prompt, and grading pipeline can be pointed at either.

Results and analysis: [`docs/learnings/2026-07-26-swe-bench-lite-vs-opencode.md`](../../docs/learnings/2026-07-26-swe-bench-lite-vs-opencode.md).

## Requirements

- **Linux or WSL** — the `swebench` package imports the Unix-only `resource` module.
- **Docker** — each task pulls a ~1.5 GB evaluation image (~40 GB for the full set).
- An OpenAI-compatible model endpoint.

```bash
uv venv ~/.sweb/venv --python 3.12
uv pip install --python ~/.sweb/venv/bin/python swebench
```

## Run

```bash
export BENCH_MODEL_API_KEY=…          # never committed; read from the environment only
export BENCH_MODEL_BASE_URL=https://api.openai.com/v1   # any OpenAI-compatible endpoint
export BENCH_MODEL=gpt-4.1

bash container/_setup_and_run.sh --harness dream --max-sprints 5 --timeout 1500
bash container/_setup_and_run.sh --harness opencode --timeout 1500
```

Both passes are **resumable** — completed instances are skipped on restart. For long runs,
detach them (`setsid nohup … &`) so an editor or shell teardown cannot kill them mid-task.

## Grade and compare

```bash
python grade.py   --preds results/dream/predictions.jsonl    --run-id dreamFULL
python grade.py   --preds results/opencode/predictions.jsonl --run-id ocFULL
python stats.py                     # per-task table, distributions, head-to-head
python compare.py --run-id full     # writes COMPARISON.md + comparison.json
```

## How a task is run

1. Pull `swebench/sweb.eval.x86_64.<instance>`; `/testbed` holds the repo at `base_commit`.
2. Apply **and commit** the oracle `test_patch` before the agent starts. SWE-bench's
   `FAIL_TO_PASS` tests are *added* by that patch — without this step the acceptance tests do
   not exist and no agent can verify its own work.
3. Install the harness into the container (`dream` as a built wheel in an isolated venv;
   `opencode` as its single binary) and run it against `/testbed`.
4. `model_patch` = `git diff` versus the post-oracle commit, **excluding test files and
   harness scaffolding**, so an agent cannot score by editing tests.
5. Grade with `swebench.harness.run_evaluation`, which re-applies the pristine `test_patch` —
   so step 2 cannot inflate the score.

**This makes the benchmark oracle-assisted**: both agents are told the acceptance test
command. Resolve rates are therefore much higher than published SWE-bench leaderboards and
are **not** comparable to them. The only valid reading is harness-vs-harness.

## Layout

```
build_tasks.py       stratified 25-task selection  -> tasks.jsonl
container/
  run_container.py   orchestrator: image, container, agent, patch extraction, metrics
  dream_entry.py     runs inside the container; public dream API only
  _setup_and_run.sh  launcher (creates the swebench venv, then runs the orchestrator)
  opencode_config.template.json
grade.py             official SWE-bench Docker grading
stats.py             per-task table + distributions
compare.py           COMPARISON.md + comparison.json
results/<harness>/   predictions.jsonl, metrics.jsonl, grading report
```

`tasks.jsonl` is committed so runs are reproducible. To regenerate the selection:

```bash
uv run --with datasets python build_tasks.py --n 25
```

`dream_entry.py` deliberately contains **no agent logic** — it constructs a harness and calls
`run_task()`. If a fair comparison ever seems to need wrapper logic here, that is a gap in the
harness itself and belongs in `src/dream/`.
