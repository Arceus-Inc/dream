# Datasets for testing the dream harness on long-running agent tasks

*Generated: 2026-06-09 · Sources: 18 · Confidence: High on landscape, Medium on exact access/license details (verify per-dataset before use)*

## Executive summary

dream's `run_task(intent)` has a specific shape: **natural-language brief → git worktree → planner → sprint loop → generator writes/edits code → evaluator verifies**. The evaluator is only as good as the **test oracle** it can run (we already learned a clean run needs a git-initialised worktree, an exec-capable sandbox, and a way for the evaluator to see/verify the work). So the right datasets are ones that ship a **repo + a runnable test suite**, which map directly onto dream's loop.

Three task *shapes* fit, and the curated set below covers all three in graded tiers:

1. **Greenfield "build from a brief + verify with tests"** — dream's native shape. Best fits: hand-authored smoke briefs, Aider Polyglot (Exercism), **NL2Repo-Bench / ProjectEval** (build an installable library from an NL spec).
2. **Repo bug-fix "issue + repo → patch that passes hidden tests"** — the industry-standard credibility tier: **SWE-bench Verified / Lite** (FAIL_TO_PASS oracle, Docker harness). SWE-bench **Pro** for contamination-resistant numbers.
3. **Long-horizon, multi-domain terminal tasks** — **Terminal-Bench 2.0** (89 hard tasks; frontier agents score <65%). This is the literal "long-running agent task" stressor — it will find where dream's sprint/heartbeat/limit/budget machinery breaks.

**Recommendation:** build a thin `dream-eval` adapter (intent + git-init worktree + `verification_steps` → `run_task` → score = tests green) and run **Tier 0–1 first** (deterministic, cheap, fast iteration on the API), then a **SWE-bench Verified subset** for a credible number, then a **Terminal-Bench subset** to find breakpoints. Wire a **deterministic test oracle** into the contract's `verification_steps` so the evaluator gets a hard pass/fail signal instead of LLM-judging unverifiable criteria.

---

## 1. The 2026 benchmark landscape (what fits, what doesn't)

### Repo bug-fix (the standard)
- **SWE-bench Verified** — 500 human-filtered real GitHub issues across 12 Python repos; the de-facto agentic-coding standard. Oracle = `FAIL_TO_PASS` + `PASS_TO_PASS` unit tests applied in a per-instance Docker image; grading = RESOLVED / FAIL / REGRESSION / EMPTY_PATCH ([CallSphere 2026 recipe](https://callsphere.ai/blog/swe-bench-evaluating-agentic-coding-agents), [OpenAI Verified](https://openai.com/index/introducing-swe-bench-verified/)). Frontier ~77–94% Verified ([BenchLM](https://benchlm.ai/coding), [LocalAIMaster](https://localaimaster.com/models/swe-bench-explained-ai-benchmarks)).
- **SWE-bench Lite** — 300 single-file-fix instances; cheapest credible tier (~$68/run on Sonnet 4.5, 20-turn budget) ([CallSphere](https://callsphere.ai/blog/swe-bench-evaluating-agentic-coding-agents)).
- **SWE-bench Pro** — 1,865 tasks across Python/Go/TS/JS incl. **private proprietary repos**, so it's contamination-resistant; same models drop ~half (Opus 4.5: 80.9% Verified → 45.9% Pro) ([Morph](https://www.morphllm.com/swe-bench-pro)). Use for a *trustworthy* number, not iteration.
- **Avoid SWE-bench Full** (2,294, contains unsolvable cases) — quoting full-suite numbers in 2026 signals naivety ([CallSphere](https://callsphere.ai/blog/swe-bench-evaluating-agentic-coding-agents)).

### Greenfield project-from-spec (dream's native shape)
- **NL2Repo-Bench** — build a **complete, installable Python library from an NL requirements doc**; scored on installability (`pip install`), import verification, and functional test suites ([arXiv 2512.12730](https://arxiv.org/pdf/2512.12730), repo `multimodal-art-projection/NL2RepoBench`). This is the closest published match to `run_task`.
- **ProjectEval / ProjDevBench / ProdCodeBench** — end-to-end project construction from project-level requirements, "build and run the complete system"; ProjectEval + DevBench support **automatic** evaluation ([ProjectEval 2503.07010](https://arxiv.org/pdf/2503.07010), [ProjDevBench 2602.01655](https://arxiv.org/pdf/2602.01655)).
- **Aider Polyglot** — 225 Exercism exercises across C++/Go/Java/JS/Python/Rust; each task = a solution stub + a test file; **2 attempts with test feedback on retry** — mirrors dream's `needs-changes` sprint retry exactly ([Epoch](https://epoch.ai/benchmarks/aider-polyglot), [GitHub Aider-AI/polyglot-benchmark](https://github.com/Aider-AI/polyglot-benchmark)). Lightweight, no Docker.

### Long-horizon / multi-step (the stressor)
- **Terminal-Bench 2.0** — 89 hand-crafted, human-verified terminal tasks (sci-computing, SWE, ML, security, sysadmin, data); each has a unique env + human solution + comprehensive tests; end-to-end workflows, **frontier <65%**; one task takes an expert ~24h ([arXiv 2601.11868](https://arxiv.org/abs/2601.11868), ICLR 2026).
- **SWE-Lancer** — 1,400+ real Upwork freelance tasks ($1M+ payouts), individual + managerial; "did the deliverable earn its bounty" ([BirJob](https://www.birjob.com/blog/agent-benchmarks-2026)).
- **SWE-EVO / NL2Repo** — multi-version software *evolution* across release notes/commit history; the hardest long-horizon coding frontier ([SWE-EVO 2512.18470](https://arxiv.org/html/2512.18470v5)).
- Adjacent (not coding, skip for dream): GAIA, OSWorld, τ²-bench, WebArena, METR HCAST/time-horizons ([decodethefuture](https://decodethefuture.org/en/ai-agent-benchmarks-2026/), [Awesome Agents](https://awesomeagents.ai/leaderboards/agentic-ai-benchmarks-leaderboard/)).

### How the pros run it (transferable to dream)
The SWE-bench harness pattern is exactly dream's loop: repo mounted at a pre-fix commit → agent gets the **issue text** + file/exec tools → produces a diff → harness runs **hidden tests deterministically** ([CallSphere](https://callsphere.ai/blog/swe-bench-evaluating-agentic-coding-agents)). The internal-eval recipe (mine 6-month merged-PR window, keep PRs that added tests, hide the fix, containerize) is how to build a **private, contamination-free dream eval** later.

---

## 2. Curated testing set for dream (5 tiers)

Counts are deliberately small — enough signal to iterate the API, not a leaderboard run.

| Tier | Source (subset) | n | Task shape | Oracle | dream wiring | Cost | License/access |
|---|---|---|---|---|---|---|---|
| **0 · Smoke** | Hand-authored briefs (extend `run_task_demo.py`) | 3–5 | greenfield build + test | pytest you ship in the brief | git-init worktree + unrestricted sandbox; `verification_steps=[pytest]` | ~free | yours |
| **1 · Function/multi-file** | **Aider Polyglot** (Exercism) | 15–20 | implement to a stub + run lang tests | provided test file per exercise | intent = problem statement; copy stub+tests into worktree; verify = run lang test | low (no Docker) | Exercism OSS — verify per track |
| **2 · Greenfield project** | **NL2Repo-Bench** / **ProjectEval** | 8–10 | build installable lib from NL spec | `pip install` + import + functional tests | intent = requirements doc; verify = install + run test suite | med | GitHub (`NL2RepoBench`, `ProjectEval`) |
| **3 · Repo bug-fix (credibility)** | **SWE-bench Verified** | 15–25 | issue → patch passing hidden tests | FAIL_TO_PASS + PASS_TO_PASS (Docker) | clone repo @ base commit into worktree; intent = issue text; verify = harness FAIL_TO_PASS | ~$60–160/300–500 (scale to subset) | HF `princeton-nlp/SWE-bench_Verified` |
| **4 · Long-horizon stress** | **Terminal-Bench 2.0** | 5–8 | end-to-end terminal workflow | per-task test suite in the env | run inside the task's env; intent = task brief; expect low pass | med–high | GitHub `terminal-bench` |

**Selection rule for subsets:** within each tier, pick across difficulty + language + domain (don't cherry-pick easy), and *log what you dropped* so the number isn't silently inflated. For SWE-bench Verified, sample stratified by repo and by the official difficulty annotation.

---

## 3. How each tier maps onto dream's API

The adapter is the same every tier — only the "materialize" and "verify" steps differ:

```
for task in tier:
    wt = mktemp(); git init wt
    materialize(task, wt)              # stub+tests | repo@base | requirements.md
    write wt/.harness/sandbox.toml     # tier="unrestricted", confirm_unrestricted=true
    result = await harness.run_task(
        intent=task.brief_or_issue,
        worktree_root=wt,
        verification_steps=task.verification,   # <-- deterministic oracle
        max_sprints=task.budget,
        observer=StdioObserver,
    )
    score = run_oracle(task, wt)        # pytest green | FAIL_TO_PASS | pip install+tests
```

This reuses everything in `examples/run_task_demo.py` (git-init + unrestricted sandbox + StdioObserver) — Tier 0 is literally that demo with more briefs.

---

## 4. Improvements this testing will surface (hypotheses to validate)

From our earlier live runs + the harness design, expect these to show up and be the actual work:

1. **Deterministic oracle in `verification_steps`.** The evaluator currently LLM-judges and the read-only role can't run pytest — so it loops `needs-changes`. Feeding a real test command as the contract's verification step (which the runner executes and hands the evaluator) converts a soft judge into a hard pass/fail. **This is the highest-leverage fix** and these datasets force it.
2. **Evaluator's ability to *act* on the oracle.** Either give the evaluator a read-only test-run capability, or have the runner run `verification_steps` deterministically and pass results to the evaluator verdict.
3. **Budget / limit / heartbeat tuning for Tier 4.** Long-horizon tasks will trip `SessionLimiter`, turn timeouts, and the coma heartbeat — exactly where to learn the right defaults.
4. **Planner granularity under real briefs** (we already curbed over-decomposition; Tier 2 greenfield specs will test it at scale).
5. **Repo-mode ergonomics** (Tier 3): cloning at a base commit, large-repo `read_file`/`bash` performance, diff extraction for scoring.

---

## Key takeaways

- **Don't invent a benchmark — adapt three that already fit dream's loop:** Aider Polyglot (cheap retry-loop signal), SWE-bench Verified (credible number), Terminal-Bench 2.0 (long-horizon breakpoints). Add NL2Repo-Bench for the greenfield shape that *is* `run_task`.
- **The test oracle is the product.** Every tier here ships runnable tests; wiring them into `verification_steps` is both the way to score *and* the biggest improvement to dream's evaluator.
- **Start cheap and deterministic (Tier 0–1), then buy credibility (Tier 3), then find breakpoints (Tier 4).** Use SWE-bench **Pro/private** only when you need a contamination-free headline number.

## Risks & caveats

- **Contamination:** Verified is partly in model training data — fine for iteration, not for headline claims; use Pro or a private mined set for those.
- **Docker dependency** for SWE-bench (40-min first-time image pull); Terminal-Bench tasks are also containerized envs.
- **License care:** Exercism content (Aider Polyglot) and SWE-bench Pro's private repos have usage terms — verify before redistributing a curated subset.
- **Cost:** scale subset sizes to budget; SWE-bench Lite ~$68/300 on Sonnet 4.5 is the reference unit.
- **Access details flagged Medium-confidence:** confirm exact HF/GitHub IDs + licenses at use time (esp. Terminal-Bench org and NL2Repo-Bench availability).

## Sources

1. [CallSphere — SWE-bench in 2026: evaluate like Anthropic/OpenAI](https://callsphere.ai/blog/swe-bench-evaluating-agentic-coding-agents) — practical harness recipe, oracle, costs, internal-eval mining.
2. [OpenAI — Introducing SWE-bench Verified](https://openai.com/index/introducing-swe-bench-verified/) — 500 human-filtered instances.
3. [Morph — SWE-bench Pro leaderboard](https://www.morphllm.com/swe-bench-pro) — 1,865 tasks, contamination-resistant, score drop.
4. [BenchLM — SWE-bench & LiveCodeBench leaderboard (Mar 2026)](https://benchlm.ai/coding) · [Local AI Master](https://localaimaster.com/models/swe-bench-explained-ai-benchmarks) — current scores.
5. [Terminal-Bench (arXiv 2601.11868, ICLR 2026)](https://arxiv.org/abs/2601.11868) — 89 long-horizon terminal tasks, env+tests, <65% frontier.
6. [Aider Polyglot — Epoch AI](https://epoch.ai/benchmarks/aider-polyglot) · [GitHub Aider-AI/polyglot-benchmark](https://github.com/Aider-AI/polyglot-benchmark) — 225 Exercism tasks, 2-attempt retry.
7. [NL2Repo-Bench (arXiv 2512.12730)](https://arxiv.org/pdf/2512.12730) — installable lib from NL spec; install+import+tests oracle.
8. [ProjectEval (arXiv 2503.07010)](https://arxiv.org/pdf/2503.07010) · [ProjDevBench (2602.01655)](https://arxiv.org/pdf/2602.01655) — end-to-end project build, auto-eval.
9. [DevBench (arXiv 2601.11895)](https://arxiv.org/html/2601.11895v2) · [SWE-EVO (2512.18470)](https://arxiv.org/html/2512.18470v5) — developer-informed / software-evolution.
10. [SWE-bench++ (arXiv 2512.17419)](https://arxiv.org/html/2512.17419v1) · [UTBoost (2506.09289)](https://arxiv.org/pdf/2506.09289) — scalable generation & rigorous eval of coding agents.
11. [decodethefuture — AI Agent Benchmarks 2026](https://decodethefuture.org/en/ai-agent-benchmarks-2026/) · [Awesome Agents leaderboard](https://awesomeagents.ai/leaderboards/agentic-ai-benchmarks-leaderboard/) — GAIA/OSWorld/τ²/WebArena/METR landscape.
12. [BirJob — Beyond SWE-bench 2026](https://www.birjob.com/blog/agent-benchmarks-2026) — Terminal-Bench/Aider/SWE-Lancer overview.

## Methodology

12 web queries across the agentic-coding-benchmark, long-horizon-agent, and project-from-spec spaces; deep-read 3 key sources (Aider Polyglot repo, the SWE-bench-2026 evaluation recipe, NL2Repo-Bench). Sub-questions: (a) SWE-bench family & current scores; (b) Terminal-Bench / long-horizon; (c) greenfield project-from-spec with auto-eval; (d) Aider Polyglot / SWE-Lancer; (e) how production teams run the harness/oracle. Mapped findings onto dream's `run_task` loop and the constraints learned from live runs. **Round 2** verified exact access/schemas for the curated tiers (appendix below).

---

## Appendix: exact access, schemas & how to materialize into a dream worktree

> Confidence: High on IDs/schemas (pulled from the dataset cards/repos); **verify licenses on each card before redistributing a subset.**

### Tier 1 — Aider Polyglot
- **Access:** GitHub `Aider-AI/polyglot-benchmark` ([repo](https://github.com/Aider-AI/polyglot-benchmark)); 225 Exercism exercises across C++/Go/Java/JS/Python/Rust. Harness README lives in the main `Aider-AI/aider` repo under `benchmark/`.
- **Per-task layout:** `{lang}/exercises/practice/{name}/` → instructions (markdown) + a **stub implementation file** + a **separate test file** + config meta ([Epoch](https://epoch.ai/benchmarks/aider-polyglot)).
- **Materialize → dream:** copy the task dir into the worktree; `intent` = instructions + "implement the stub so the tests pass"; `verification_steps` = the language's test command (`pytest` / `go test ./...` / `cargo test` / `npm test` / …); **pass = tests green.** The benchmark's "2 attempts with test feedback on retry" maps onto dream's `needs-changes` sprint retry.
- **License:** Exercism content terms — verify per track.

### Tier 2 — NL2Repo-Bench (greenfield, closest to `run_task`)
- **Access:** GitHub `multimodal-art-projection/NL2RepoBench` ([repo](https://github.com/multimodal-art-projection/NL2RepoBench)); **104 tasks**, 9 Python-library categories, repos **300–120K LOC**, difficulty Easy/Medium/Hard. Open-source: dataset + **Docker envs** + eval toolkit ([arXiv 2512.12730](https://arxiv.org/html/2512.12730v1), [author thread](https://x.com/GeZhang86038849/status/2000781746657284377)).
- **Per-task:** requirements doc (project-level description + support info + **API-level usage guide**) + upstream test suite.
- **Materialize → dream:** empty worktree; `intent` = the requirements doc; `verification_steps` = `pip install -e . && pytest` against the upstream suite; **score = installability + import + functional tests.** Default runner is OpenHands headless (`config.toml`) — swap in dream as the agent.

### Tier 3 — SWE-bench Verified (credibility)
- **Access:** HF `princeton-nlp/SWE-bench_Verified` (500) ([card](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)); Lite = `princeton-nlp/SWE-bench_Lite` (300). Official Docker harness: `princeton-nlp/SWE-bench`.
- **Schema (per instance):** `repo`, `instance_id` (`owner__repo-PR#`), `base_commit`, `patch` (gold fix — hidden from agent), `test_patch`, `problem_statement`, `hints_text`, `version`, **`FAIL_TO_PASS`**, **`PASS_TO_PASS`**, `environment_setup_commit`, `difficulty`.
- **Materialize → dream:** clone `repo` at `base_commit` into the worktree (broken state, gold `patch` withheld); `intent` = `problem_statement`; oracle = apply `test_patch`, run `FAIL_TO_PASS` (must flip to pass) + `PASS_TO_PASS` (must stay green). For a *canonical* score, emit `{"instance_id","model_patch": <diff>}` JSONL and grade with the official Docker harness; for fast iteration, run the tests in-worktree.
- **License:** check the HF card (SWE-bench is permissively licensed; confirm).

### Tier 4 — Terminal-Bench 2.0 (long-horizon stress)
- **Access:** GitHub `laude-institute/terminal-bench` (Harbor framework); 2.0 distributed via the Harbor registry ([repo](https://github.com/laude-institute/terminal-bench), [tbench.ai](https://www.tbench.ai/)).
- **Per-task (Harbor format):** `task.yaml` (NL description, names files/vars referenced by tests) + a `tests/` subfolder **copied into the container *after* the agent runs**.
- **Integration note — this tier inverts:** instead of materializing a task into a dream worktree, the clean path is to **register dream as a Harbor agent** (the repo already ships a `claude-code` agent: `harbor run -d terminal-bench@2.0 --agent <your-agent> --model <m>`; single task: `tb run --task-id "<name>" --agent <agent> --model <m>`). So Tier 4 also tests dream's **agent-under-an-external-harness** path, not just `run_task`.
- **Expectation:** frontier <65% — the value is *where dream breaks* (turn timeouts, coma heartbeat, `SessionLimiter`, budget), not the pass-rate.

### Build order (cheapest signal first)
1. **Tier 0 + 1** (no Docker, deterministic) → iterate the `run_task` / `verification_steps` API.
2. **Tier 2** (greenfield) → exercises the native shape + planner granularity.
3. **Tier 3** Verified subset (Docker) → a credible number.
4. **Tier 4** Terminal-Bench subset → breakpoints (and the dream-as-Harbor-agent path).
