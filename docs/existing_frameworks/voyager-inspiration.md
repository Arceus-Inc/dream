# Voyager → Long-Running Self-Evolving Agent Harness: Inspiration Notes

Source: [MineDojo/Voyager](https://github.com/MineDojo/Voyager). Distilled patterns relevant to a months-long, multi-agent, self-evolving "company-builder" harness.

## Core Architecture

Four agents, one shared world, ~20-line orchestration loop ([voyager.py](voyager/voyager.py)):

| Voyager | Role | Company-builder analog |
|---|---|---|
| `CurriculumAgent` | What to do next | Sprint/backlog planner |
| `ActionAgent` | Write executable code | Specialist worker |
| `CriticAgent` | Did it work? | QA / governance |
| `SkillManager` | Store reusable verified code | Playbook / capability library |

Loop: `propose → rollout → critic → if success: skill.add → curriculum.update`. Each iteration's only persistent side effect is one new skill + one task status. **That minimalism is what enables months of operation.**

## Orchestration — patterns to steal

1. **Reconstruct-from-disk-only state.** `resume=True` rebuilds every agent from `ckpt_dir/` JSON + Chroma vectordb ([voyager.py:43-49](voyager/voyager.py#L43-L49)). No in-memory session object. Resume is free.
2. **Bounded retry per task** (`max_retries=4`). Failed tasks are marked and skipped. No infinite loops, no escalation.
3. **Try/except around the entire rollout** ([voyager.py:325-345](voyager/voyager.py#L325-L345)) → hard env reset preserving inventory/position → continue. A panicked agent never poisons company state.
4. **Two-phase planning**: `propose_next_task` (open-ended) vs `decompose_task` (goal-directed). Same agent, different prompt. You'll want both for "what should the company work on" vs "break down this OKR."
5. **Pause/unpause the world around LLM calls** ([bridge.py](voyager/env/bridge.py)). Deterministic — dependents block on a deliberating agent rather than racing on stale state.

## Validation (Critic) — underrated

1. **Critic sees outcome state, NOT code.** Forces semantic verification, prevents review collusion. Feed it diffs/metrics/artifacts, not the worker's reasoning.
2. **Critique is a first-class prompt slot on retry** ([voyager.py:236-242](voyager/voyager.py#L236-L242)). Retry ≠ same prompt; retry = same prompt + structured complaint.
3. **Schema'd JSON output** `{success, reasoning, critique}` with `fix_and_parse_json` bounded retry ([critic.py:96-114](voyager/agents/critic.py#L96-L114)). This becomes your auditable governance log forever.
4. **Examples > rules in prompts.** [critic.txt](voyager/prompts/critic.txt) is ~80% concrete I/O examples. Token-expensive, ambiguity-killing.
5. **Manual-mode escape hatch on every agent** (`mode="manual"`). Every autonomous decision is replaceable by a human prompt with no code change.

## Memory — three typed stores, not generic RAG

1. **Skill memory = code-as-memory** ([skill.py](voyager/agents/skill.py)). Embed the LLM-generated *description*; inject the *code*. Natural-language retrieval, executable payload.
2. **QA cache** ([curriculum.py:330-360](voyager/agents/curriculum.py#L330-L360)). Self-built FAQ: question→answer dict + question embeddings, 0.05 similarity threshold for cache hit. Massive cost saver over months.
3. **Episodic memory = "chest memory"** ([action.py:40-55](voyager/agents/action.py#L40-L55)). Tiny typed JSON of one specific fact class (chest contents @ coords), re-rendered into every prompt. **Lesson: pick 3-5 fact types that matter (services, owners, OKRs, customers); don't build "remember everything."**
4. **Memory consistency asserted at boot**: `vectordb.count() == len(skills)` ([skill.py:42-47](voyager/agents/skill.py#L42-L47)). Saves you at month 4 when something silently corrupts.

## Skill Evolution

1. **Critic success → ActionAgent's last code → SkillManager**, named & described by LLM ([skill.py:58-99](voyager/agents/skill.py#L58-L99)).
2. **Versioning by collision**: `craftIronHelmet` → `craftIronHelmetV2.js` on disk, vectordb keeps only latest. Lineage preserved on disk, only "current best" reachable. Git-without-git. See [skill_library/trial1/skill/code/](skill_library/trial1/skill/code/).
3. **Compositionality by prompt convention**, not graph engine. New skills can call existing skills by name. Action prompt explicitly says "your function will be reused."
4. **Top-K retrieval into system message every turn** — LLM sees curated subset, not whole library. Critical for context-window survival over months.
5. **Failures stored flat as text** ("Failed tasks: X, Y, Z" rendered into curriculum prompt). No structural learning from failures — works fine.
6. **Warm-up + prompt dropout** ([curriculum.py:91-108](voyager/agents/curriculum.py#L91-L108)). Observation fields gated by progress count; non-zero gates have 0.8 random-include probability. Don't dump every memory into every prompt; gate by progress, randomly drop for robustness.

## Runtime / Sandbox

1. **Generated code runs in a separate Node process via HTTP** ([bridge.py](voyager/env/bridge.py)). Process boundary = blast radius boundary. `SubprocessMonitor` restarts on crash. Never `exec()` LLM code in your main process.
2. **AST-validate before exec** ([action.py:188-225](voyager/agents/action.py#L188-L225)). Babel-parse, extract last async function with `bot` param, validate signature, 3 retries on parse fail.
3. **Trusted hand-written control primitives** ([voyager/control_primitives/](voyager/control_primitives/)). LLM composes vetted tools (`mineBlock`, `craftItem`); never invokes raw side effects. Library evolves on top; primitives don't.

## What Voyager LACKS (you'll need to add)

- **Multi-agent coordination** — strictly sequential, one bot.
- **Hierarchy / delegation** — four peer agents in a fixed pipeline.
- **Governance beyond outcome quality** — no policy/permission/budget layer.
- **Skill GC** — skills only added/bumped, never deprecated or merged. You'll need: prune-if-never-retrieved, merge-near-duplicates.
- **Cross-trial transfer** — each `ckpt_dir` is isolated.
- **Long-horizon goal stack** — curriculum is "what's interesting next," not "what advances the OKR."

## Top 7 to Steal (ranked)

1. Reconstruct-from-disk-only state per agent.
2. Code-as-skill, description-embedded, name-keyed, versioned-on-disk.
3. Retrieve-top-K skills into the action prompt every turn.
4. Critic sees outcome (not code); structured JSON; critique fed as first-class retry slot.
5. Bounded retry per task + hard reset on exception → loop never dies.
6. Self-built QA cache with vector dedup.
7. AST-validate generated code; run in a kill-able separate process.

**Underlying lesson:** Voyager's novelty is composing four cheap pieces with disciplined disk-backed state. For a months-long harness, *that discipline is the asset* — not any single agent.
