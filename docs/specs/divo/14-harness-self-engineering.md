# 14 — Harness Self-Engineering (the AHE meta-loop)

**One-liner:** You usually can't retrain the frontier model, so recurse on the thing you *do* control — the
**harness**. Decompose the scaffold into orthogonal, git-tracked components; let a separate **Evolve Agent**
edit them under a **Change Manifest** that makes every edit a falsifiable hypothesis (`failure_evidence` /
`root_cause` / `targeted_fix` / `predicted_impact`), then write back a `keep | revert | partial` **verdict**
next generation. The trace — not the pass rate — is the unit every step reasons over. Start minimal (2
components), grow only when a trace earns it, and when the same failure survives the same-layer fix, **roll
back and re-solve at a different layer.**

**Sources (source of truth):** `self_improving.md` **Part IV — "Evolving the Harness, Not the Model"** (§§33–40):
the **sharp claim** that the harness is a *learnable, transferable* artifact (freeze the model, evolve only the
scaffold — Terminal-Bench 2 pass@1 69.7%→77.0%, beats hand-written Codex, and **transfers to other benchmarks
and four other base models with zero re-evolution** → it encoded general SWE experience, not benchmark tricks;
"Harness is the dataset"); the **7-component orthogonal decomposition** (§34: system rules / tool descriptions /
tool implementations / middleware / skills / sub-agents / long-term memory — each its own git-tracked file,
**orthogonal** so a failure is attributable to *one layer*, plus the forbidden anti-patterns: don't write tool
logic into system rules, don't stuff memory into system rules, don't have two components do one job, never
change tool code without updating its description); the **three observability layers** (§35: *component
observability* = every component is a git diff, auditable + revertible; *experience observability* = the Agent
Debugger that distills 10M-token raw traces into layered reports `analysis/overview.md` + `analysis/detail/{task}.md`
where **every claim links back to the originating raw trace**; *decision observability* = the Evolve Agent that
may write **only** inside `workspace/`, and "the trace, not the pass rate, is the unit every later step operates
on"); the **Change Manifest** (§36: four mandatory fields, the `root_cause`≠`failure_evidence` distinction —
"tool errored" is not a root cause, "the description didn't declare a param the implementation supports" *is* and
names exactly one component — then the next-generation **verdict** `keep`/`revert`/`partial`, "an edit whose
prediction fails gets rolled back"); the **staggered-generation loop** (§37: `evaluate → analyze → improve →
verify`, terminates on `target_pass_rate` default 0.95 or `max_iterations`; each `runs/iteration_NNN/` holds two
generations — `input/` produced by loop NNN-1, `evolve/` written by loop NNN and evaluated *next* loop — so
pass↔fail flips attribute back to the prior loop's edits in `change_evaluation.json`; falsification is baked into
the cadence, not a manual review); **minimal start + component extrapolation** (§38: a working harness needs only
2 components — system rules + one tool; everything else added *only when earned*; and if the same failure
persists 2+ generations and the same-layer fix isn't working, **roll back and re-solve at a different layer** — a
search over *which layer owns the bug*); and the **§40 borrowings** Arceus should adopt (Change Manifest as the
proposal-queue schema; trace-not-score as the dream-phase unit; component-extrapolation as a governance rule). That
part's lineage — [AHE] (Agentic Harness Engineering, Fudan & Peking Univ., 2026, arXiv 2604.25850) as the *empirical
keystone* of the whole `self_improving.md` synthesis, and its near-1:1 Arceus mapping in §39 — is the conceptual
authority.
· Cross-refs that bound this spec: `#01` (repo-as-system-of-record — *this is* AHE's "component observability";
every harness component is a git-tracked file, so every self-edit is a diff with blame + rollback) · `#03` (the
per-turn FSM `read → plan → act → verify → record` — spec 14 is that same loop run **one altitude up**, where the
"task" being worked is *the harness itself*) · `#04` (context budgeting — the Agent Debugger's trace distillation
is the offload contract applied to evolution: 10M-token traces never enter the optimizer's context raw) · `#05`
(the tool descriptions + implementations that are components 2 & 3; the Evolve Agent's edit/distill tools live in
the action space) · `#07` (verification + "agent struggle is signal" — the failure trace that becomes a Change
Manifest's `failure_evidence`; the rubric/verdict that the Evolve Agent's `verify` step consumes; tech-debt
auto-filing is the un-falsified precursor this spec upgrades) · `#09` (the autopilot/cron that *runs* the evolve
loop off the hot path — the evolve loop is a `kind: harness-evolve` cron candidate) · `#10` (the Evolve Agent **is**
an evaluator-isolated subagent; orchestration owns when it spawns) · `#11` (the **boundary**: memory/skill *content*
evolution lives there, scaffold-*structure* evolution + the falsification machinery lives here — see
`## Relationship to #11`) · `#13` (governance — the Evolve Agent's `workspace/` write-boundary is a sandbox tier;
the constitution (`core-beliefs.md` / product specs) is **outside** `workspace/` and cannot be self-edited;
component-extrapolation becomes a standing order).

**Reference (grounding only, not authority):** [openharness] has **no analogue** for the evolve meta-loop — it
ships a runtime, not a self-engineering pipeline — so the grounding here is the **thinnest of any spec**, by
nature rather than by omission. What little it lends is structural, not behavioural: the `plugins/loader.py`
component-as-file model and `services/autodream/backup.py` `create_memory_backup` + `diff_memory_dirs →
{added, removed, changed}` triad (seen in `#11`) ground the *mechanical* shape of a component diff + safe-net
rollback the Change Manifest's verdict rides on. **Everything load-bearing here — the 7-component decomposition,
the Change Manifest, the staggered falsification loop, trace-distillation, component-extrapolation — is the AHE
paper's contribution carried through `self_improving.md`, with no OpenHarness implementation precedent.** This
spec is honest that it specifies a pipeline OpenHarness does not have.

---

## Why this matters

The field spent 2023–2025 treating the **model** as the unit of improvement: better weights → better agent. AHE
inverts it and *measures the inversion* — freeze the base model, evolve only the scaffold, and get a jump bigger
than most fine-tunes deliver, **that transfers to other models and benchmarks without re-evolution.** If the
harness were overfitting, transfer would collapse; it doesn't, so the harness encoded general engineering
experience. That is the strongest available answer to "is self-improvement real or hype?" — a controlled,
ablated, leaderboard-ranked, transfer-tested *yes*. Three failures kill a naive attempt at this, and each maps
to one of this spec's pillars:

1. **"The agent is bad" is unactionable.** If the harness is one blob of prompt+tools, a capability complaint
   ("it can't paginate search results") can't be pinned to a cause, so the fix is a vibe-reword. → **Orthogonal
   decomposition** (7 git-tracked components) converts the complaint into "component 2, the tool *description*,
   omits the `page_size` param the implementation already supports" — a single-file, revertible fix attributable
   to one layer.
2. **You can't improve on a scalar.** A pass rate tells you *that* you failed, never *why*; and one iteration's
   raw traces routinely exceed **10 million tokens**, unreadable by any optimizer. → **The trace is the unit**,
   distilled by an Agent Debugger into sourced root-cause digests (every claim links back to the raw trace), so
   the evolver reasons over causes, not counts.
3. **Prompt engineering is unfalsifiable by default.** "Let me reword this and see" never gets rolled back when
   it silently regresses three other tasks. → **The Change Manifest** makes every edit a hypothesis with a
   predicted impact, and the *next generation* writes back a `keep | revert | partial` verdict. Popper in a
   prompt repo: a change survives only if it made a prediction and the prediction came true.

The through-line — the same law `self_improving.md` finds at the org, lab, and model altitudes — is **the
highest-leverage loop is the one pointed back at its own substrate.** Spec 14 is `#03`'s turn loop run one level
up, where the work product *is the harness*, and `#11`'s dream phase is the outer cron that drives it. Two
discipline-defining defaults keep it safe: the Evolve Agent writes **only** inside `workspace/` (the constitution
is out of reach — this is what prevents the belief drift of six weeks of unsupervised self-editing), and **minimal
start** means every component that exists exists because a trace proved it was needed — nothing speculative.

## Scope

**In:**
- The 7-component decomposition of the harness as orthogonal, git-tracked files, and the forbidden
  cross-component anti-patterns.
- The three observability layers (component / experience / decision) and the Agent Debugger's trace-distillation
  contract.
- The Change Manifest schema (four mandatory fields) and the next-generation verdict (`keep | revert | partial`).
- The staggered `evaluate → analyze → improve → verify` evolve loop, its iteration directory layout, and the
  flip-attribution mechanism.
- Termination (`target_pass_rate` / `max_iterations`) and the evolve-run report artefacts.
- Minimal-start and component-extrapolation rules.
- The Evolve Agent's `workspace/`-only write boundary and the tools it uses (distiller, manifest-writer,
  component-editor), with their observation + recovery contracts.
- The boundary with `#11` (memory/skill *content* vs scaffold *structure*).

**Out:**
- Model fine-tuning / weight updates of any kind (the premise is the model is *frozen*).
- The orchestration of *when* the Evolve Agent spawns, its concurrency, isolation mechanics (→ `#10`; it is an
  evaluator-class subagent).
- The cron scheduler that fires the evolve loop (→ `#09`; this spec defines the loop body, not the timer).
- Memory/skill *content* lifecycle — promotion gate, skill GC, wiki curation (→ `#11`).
- The verification surface + rubric the `evaluate` step calls (→ `#07`; reused, not redefined).
- Governance *enforcement* of the `workspace/` boundary and constitution protection (→ `#13`; declared here,
  enforced there).
- Multi-harness / cross-repo transfer tooling (the *transfer* is an observed property; shipping a
  port-the-harness tool is deferred).
- Automatic acceptance of evolved harnesses into the live default branch without operator review (always gated).

## Key decisions (assumed defaults)

1. **The harness is 7 orthogonal components, each a git-tracked file/dir under `workspace/`:**
   (1) **System Rules** (`workspace/system-rules.md`) — how the agent thinks/decides, its boundaries;
   (2) **Tool Descriptions** (`workspace/tool-descriptions/*.yaml`) — schema + usage + gotchas the *LLM sees*;
   (3) **Tool Implementations** (`workspace/tools/*`) — the code that runs;
   (4) **Middleware** (`workspace/middleware/*`) — pipeline hooks: intercept / transform / compact;
   (5) **Skills** (`workspace/skills/*/SKILL.md`) — reusable workflow SOPs;
   (6) **Sub-Agents** (`workspace/sub-agents/*/`) — delegatable specialist definitions;
   (7) **Long-Term Memory** (`workspace/memory/MEMORY.md`) — persistent cross-session knowledge **pointer**
   (content lifecycle is `#11`'s; this component is the *index the harness loads*).
2. **Orthogonality is enforced by anti-pattern checks**, not just convention. Forbidden: tool *logic* in System
   Rules (mixing 1 & 3); memory dumped into System Rules (context bloat every load); two components doing one
   job; a tool implementation changed without its description updated (2 & 3 drift). Each is a blocking
   `validator.finding` (`#07`/`#13`) at evolve time.
3. **Three observability layers, all repo-native:**
   - *Component* — every component is a git diff (the `#01` substrate); "what changed / can I undo it" is always
     answerable.
   - *Experience* — the **Agent Debugger** distills raw session traces (`#07` JSONL) into
     `runs/iteration_NNN/analysis/overview.md` (cross-task root causes) + `analysis/detail/{task}.md` (per-task),
     **every claim carrying a pointer back to the originating raw trace line.** Read digests by default, drill to
     raw before committing an edit.
   - *Decision* — the **Evolve Agent** may write **only** inside `workspace/`; every edit carries a Change
     Manifest.
4. **The trace, not the pass rate, is the unit.** No evolve step may act on a scalar score alone; it must cite a
   distilled trace digest that links to raw evidence. A pass-rate delta is a *trigger*, never a *justification*.
5. **Change Manifest is mandatory on every component edit** (`workspace/.../CHANGES/{manifest-id}.toml`), with
   four required fields: `failure_evidence` (what failed + trace excerpt pointer), `root_cause` (*why*, naming
   exactly one component), `targeted_fix` (the specific change), `predicted_impact`
   (`expected_fixes`/`at_risk_regressions` task lists + rationale). A `root_cause` that restates the symptom
   ("tool errored") is rejected.
6. **Every manifest gets a verdict next generation:** `keep` (predictions held), `revert` (didn't help or caused
   the predicted/observed regressions — **rolled back automatically**), `partial` (some held — adjust and
   re-file). An edit whose prediction *fails* is reverted, not kept-and-rationalised.
7. **The evolve loop is `evaluate → analyze → improve → verify`,** staggered: `runs/iteration_NNN/` contains
   `input/` (the workspace produced by loop NNN-1, just evaluated) and `evolve/` (what loop NNN writes, evaluated
   next loop). Pass↔fail flips between `input/` runs and the prior `evolve/` are attributed to the prior loop's
   manifests in `runs/iteration_NNN/change_evaluation.json`. **Falsification is structural, not a manual step.**
8. **Termination:** loop stops at `target_pass_rate` (default **0.95**) or `max_iterations` (configurable). Each
   run writes a report with per-iteration pass-rate, manifests filed, verdicts rendered, and components touched.
9. **Minimal start.** A new harness ships with **2 components**: System Rules + one tool (description +
   implementation). Middleware, skills, sub-agents, and memory are added **only when a trace earns them**.
   Evolving up from minimal beats pre-configuring all 7 (every component exists because a trace proved it
   necessary; nothing speculative).
10. **Component extrapolation.** If the same failure mode persists **2+ generations** and the same-layer fix keeps
    getting reverted, **roll back and re-solve at a different layer** (e.g. stop patching the tool *description*;
    add a *middleware* that truncates the tool's output, or a *skill* encoding the correct call sequence). The
    evolve loop searches over *which layer owns the bug*, not just *what the fix is*.
11. **The Evolve Agent is isolated** (`#10`): its own context, a read-only view of everything outside
    `workspace/`, and a write capability scoped to `workspace/` only. It cannot edit the constitution
    (`core-beliefs.md`, product specs) — those live outside `workspace/` and are governance-gated (`#13`).
12. **Evolved harnesses are never auto-promoted to the live default branch.** The evolve loop runs on a branch /
    in a worktree; merging the evolved `workspace/` into production is an operator-gated PR (`#13` auto-merge
    block on harness-component paths). Self-evolution proposes; a human disposes.

## Artefact shapes

### Evolve run directory (`runs/iteration_{NNN}/`)

```
input/                      # workspace as produced by loop NNN-1 (evaluated this loop)
evolve/                     # workspace written by loop NNN (evaluated next loop)
analysis/
  overview.md               # cross-task root causes, each → raw-trace pointer
  detail/{task-id}.md       # per-task deep dive, each claim → raw-trace pointer
change_evaluation.json      # pass↔fail flips attributed to prior loop's manifests → verdicts
report.md                   # pass-rate, manifests filed, verdicts, components touched
```

### Change Manifest (`workspace/.../CHANGES/{manifest-id}.toml`)

```toml
id              = "cm-0042"
component       = "tool-descriptions/search.yaml"   # exactly one of the 7 components
generation      = 17

failure_evidence = "T-042 failed: search returned >50 results; trace T-042#L318 shows the agent tried page=2 but the description never declared it."
root_cause       = "search supports pagination, but the description omits page_size/offset, so the LLM doesn't know it can paginate."   # WHY, names one component
targeted_fix     = "add page_size/offset to input_schema.properties; document pagination semantics."

[predicted_impact]
expected_fixes      = ["T-042", "T-057"]
at_risk_regressions = ["T-013"]
rationale           = "T-013 relies on the un-paginated default; verify it still passes."
```

### Verdict entry (in `change_evaluation.json`, written next generation)

```
manifest_id   : "cm-0042"
verdict       : "keep" | "revert" | "partial"
observed_fixes      : ["T-042", "T-057"]   # of the predicted expected_fixes, which actually flipped pass
observed_regressions: []                   # any task that flipped fail (predicted or not)
note          : "all predictions held; T-013 unaffected."
```
`revert` triggers an automatic git revert of the manifest's commit; `partial` re-opens the manifest for a
follow-up edit; `keep` closes it.

### Trace digest claim (in `analysis/*.md`)

Every bullet is `{claim} — {raw-trace pointer}` (e.g. `agent never attempted pagination — T-042#L318`). A digest
claim **without** a pointer is itself a blocking finding (the digest must be falsifiable against raw).

## Behaviours

### The evolve loop (one generation NNN)

1. **evaluate** — run the task suite against `runs/iteration_{NNN}/input/` (the prior loop's `evolve/`) using the
   `#07` verification surface + rubric. Record pass/fail per task; emit traces to JSONL.
2. **analyze** — the Agent Debugger distills this generation's traces into `analysis/overview.md` +
   `analysis/detail/{task}.md`, each claim pointer-linked to raw. It also computes pass↔fail flips vs the prior
   generation and writes `change_evaluation.json`, rendering a verdict for every prior-loop manifest. `revert`
   verdicts are git-reverted now.
3. **improve** — the Evolve Agent reads the digests (not raw traces by default), picks failures to attack, and for
   each writes a Change Manifest then applies the targeted fix **inside `workspace/` only**, committing into
   `runs/iteration_{NNN}/evolve/`. Anti-pattern checks (decision #2) run on every edit; a violation blocks the
   edit with a recovery hint.
4. **verify** — sanity-run the smoke subset against `evolve/` to catch a manifest that broke the harness outright;
   full attribution waits for the next generation's `evaluate`.
5. Repeat with NNN+1 until `target_pass_rate` or `max_iterations`; write `report.md`.

### Component editing (the high-risk path — micro-tool, manifest-gated)

Self-editing the scaffold is the highest-risk action in the whole harness, so it is a **micro-tool**
(`edit_component`) that refuses to run without a valid Change Manifest in the same turn, refuses any path outside
`workspace/`, and refuses an edit that trips an anti-pattern check. Its observation is deterministic:

```
status      : success | warning | error
summary     : "edited tool-descriptions/search.yaml under cm-0042"
artifacts   : [ "workspace/tool-descriptions/search.yaml", "workspace/tool-descriptions/CHANGES/cm-0042.toml" ]
next_actions: [ "await next-generation verdict", "run smoke verify" ]
```

### Trace distillation (the context-budget path)

`distill_traces(iteration)` reads the generation's raw JSONL and returns **pointers + digests, never the raw
bytes** (the `#04` offload contract). A digest that would exceed budget is itself summarised recursively; the raw
is always one pointer-hop away. This is the mechanism that lets a >10M-token generation be reasoned over inside a
normal context window.

### Verdict rendering & rollback

When `analyze` attributes flips, each prior manifest's `predicted_impact` is checked against `observed_*`:
predictions held → `keep`; any predicted-or-observed regression, or zero observed fixes → `revert` (auto git
revert); mixed → `partial`. **No manifest is left un-adjudicated**; an orphaned manifest (its component reverted
out from under it) is closed `stale`.

### Minimal start & extrapolation

A fresh `workspace/` has only components 1 + (one of 2/3). The Evolve Agent may *introduce* a new component only
by filing a manifest whose `root_cause` shows the missing layer is the cause (e.g. "no skill encodes the
3-call sequence, so the agent re-derives it and errs"). If a failure mode's same-layer manifests are reverted
**2 generations running**, the next manifest **must** target a different component (extrapolation), and the loop
records the layer-hop.

## Relationship to #11 (the boundary)

`#11` and `#14` are both "self-evolution under git, off the hot path, gated against drift." They are **not** the
same spec, and the split is sharp:

| | `#11` Memory & Self-Evolution | `#14` Harness Self-Engineering |
|---|---|---|
| **Evolves** | what the agent *knows* — memory tiers, wiki entries, skill *content* | how the harness is *built* — the 7-component *scaffold structure* |
| **Driver** | the **dream phase** (nightly consolidation) | the **evolve loop** (`evaluate→analyze→improve→verify`, staggered) |
| **Unit** | a memory/skill, scored by a promotion gate | a component edit, falsified by a Change Manifest verdict |
| **Discipline** | scored gate · two-occurrence rule · skill GC | Change Manifest · staggered flip-attribution · component-extrapolation |
| **Write target** | `docs/wiki/`, skill files (content) | `workspace/` (the scaffold), component files (structure) |

**The shared artefact (the §40 borrowing made concrete):** the **Change Manifest is the proposal-queue schema**.
`#11`'s dream phase already queues wiki/skill proposals and files tech-debt; this spec *upgrades that schema* so
every proposal carries `failure_evidence + root_cause + targeted_fix + predicted_impact` and earns a
`keep | revert | partial` verdict next cycle. So `#11` *consumes* the manifest format `#14` defines, and the dream
phase's merge/reject **is** the verdict step. Concretely: a wiki promotion (`#11`) and a tool-description edit
(`#14`) file the *same manifest shape*; the difference is only the `component` field's domain and which loop
adjudicates it. Where component 7 (Long-Term Memory) appears in `#14`'s list, it is the **pointer/index the
harness loads**, not the content lifecycle — that content is wholly `#11`'s. No overlap, one schema.

## Acceptance criteria

### Decomposition (MUST)

1. **MUST** represent the harness as the 7 named components, each a git-tracked file/dir under `workspace/`.
2. **MUST** keep components orthogonal — a single capability concern maps to a single owning component.
3. **MUST** block (as a `validator.finding`) the forbidden anti-patterns: tool logic in System Rules; memory in
   System Rules; two components owning one job; a tool implementation changed without its description.

### Observability (MUST)

4. **MUST** make every component edit a git diff (revertible, blame-able) — component observability.
5. **MUST** distill raw generation traces into `overview.md` + `detail/{task}.md` digests, every claim linking
   back to a raw-trace pointer.
6. **MUST NOT** let any evolve step act on a pass-rate scalar alone — every justification cites a distilled trace.
7. **MUST** confine the Evolve Agent's writes to `workspace/`.

### Change Manifest (MUST)

8. **MUST** require a valid Change Manifest (all four fields) on every component edit.
9. **MUST** reject a `root_cause` that restates the symptom rather than naming a cause + one component.
10. **MUST** render a `keep | revert | partial` verdict for every manifest in the following generation.
11. **MUST** automatically git-revert a manifest whose verdict is `revert`.
12. **MUST** leave no manifest un-adjudicated (orphans closed `stale`).

### Evolve loop (MUST)

13. **MUST** stagger generations so pass↔fail flips attribute to the prior loop's manifests in
    `change_evaluation.json`.
14. **MUST** terminate on `target_pass_rate` (default 0.95) or `max_iterations`.
15. **MUST** write a per-run report (pass-rate, manifests, verdicts, components touched).

### Minimal start & extrapolation (MUST/SHOULD)

16. **MUST** allow a working harness of only 2 components (System Rules + one tool).
17. **MUST** introduce a new component only via a manifest whose `root_cause` shows the missing layer is the
    cause.
18. **SHOULD** require, after a failure mode's same-layer manifests are reverted 2 generations running, that the
    next manifest target a different component (extrapolation), recording the layer-hop.

### Safety / governance (MUST)

19. **MUST NOT** let the Evolve Agent edit the constitution (`core-beliefs.md`, product specs) — outside
    `workspace/`.
20. **MUST NOT** auto-promote an evolved `workspace/` to the live default branch; promotion is an operator-gated
    PR (`#13`).
21. **MUST** reuse `#11`'s manifest as the proposal-queue schema rather than defining a divergent one.

## Acceptance scenarios

```gherkin
Scenario: A capability complaint is pinned to one component
  Given the agent fails task T-042 because search returns >50 results and it cannot paginate
  When the Agent Debugger analyzes the trace
  Then the root cause names exactly one component (tool-descriptions/search.yaml)
  And it cites the raw-trace pointer where the agent tried page=2.

Scenario: Edit without a manifest is refused
  Given the Evolve Agent attempts to edit a component file
  And no Change Manifest exists for the edit in this turn
  When edit_component runs
  Then the edit is refused with status=error
  And the recovery hint says a Change Manifest is required.

Scenario: Symptom-as-root-cause manifest is rejected
  Given a manifest whose root_cause is "the tool errored"
  When the manifest is validated
  Then it is rejected
  And the reason states root_cause must name a cause and one component.

Scenario: Edit outside workspace is refused
  Given the Evolve Agent attempts to write to core-beliefs.md
  When edit_component runs
  Then the write is refused
  And a governance finding is logged (the constitution is outside workspace/).

Scenario: Prediction holds → keep
  Given manifest cm-0042 predicted expected_fixes=[T-042,T-057], at_risk_regressions=[T-013]
  And the next generation flips T-042 and T-057 to pass with T-013 unaffected
  When change_evaluation.json is written
  Then cm-0042's verdict is keep.

Scenario: Prediction fails → automatic revert
  Given manifest cm-0050 predicted a fix for T-070
  And the next generation shows T-070 still failing and T-031 newly regressed
  When the verdict is rendered
  Then cm-0050's verdict is revert
  And its commit is automatically git-reverted.

Scenario: Staggered attribution
  Given iteration_017/evolve/ contains the edits of loop 17
  When loop 18 evaluates iteration_018/input/ (= loop 17's evolve/)
  Then the pass↔fail flips are attributed to loop 17's manifests
  And recorded in iteration_018/change_evaluation.json.

Scenario: No scalar-only justification
  Given the Evolve Agent wants to edit a component citing only "pass rate dropped 3%"
  When the edit is attempted
  Then it is blocked until the justification cites a distilled trace digest with a raw pointer.

Scenario: Minimal start grows only when earned
  Given a fresh workspace with only System Rules + one tool
  When the agent never fails in a way a skill would fix
  Then no skills component is introduced
  And the harness remains at 2 components.

Scenario: Component extrapolation after repeated same-layer reverts
  Given a failure mode whose tool-description manifests were reverted in generations 14 and 15
  When the Evolve Agent files generation 16's manifest for the same failure
  Then the manifest targets a different component (e.g. middleware or skill)
  And the layer-hop is recorded.

Scenario: Trace distillation respects the context budget
  Given a generation produced 12 million tokens of raw trace
  When distill_traces runs
  Then it returns digests + pointers, not the raw bytes
  And every digest claim links back to a raw-trace line.

Scenario: Evolved harness is not auto-promoted
  Given the evolve loop reached target_pass_rate on a branch
  When the run completes
  Then the evolved workspace/ is not merged to the default branch automatically
  And an operator-gated PR is opened.

Scenario: Manifest schema is shared with the dream phase
  Given the #11 dream phase queues a wiki promotion proposal
  When the proposal is written
  Then it uses the same four-field Change Manifest shape
  And earns a keep/revert/partial verdict on the next dream cycle.
```

## Tests

- `test_harness_has_seven_named_components`
- `test_components_are_git_tracked_files`
- `test_tool_logic_in_system_rules_blocked` — anti-pattern.
- `test_memory_dump_in_system_rules_blocked` — anti-pattern.
- `test_two_components_one_job_blocked` — anti-pattern.
- `test_tool_impl_changed_without_description_blocked` — 2/3 drift.
- `test_component_edit_is_a_git_diff` — component observability.
- `test_trace_distilled_into_overview_and_detail`
- `test_every_digest_claim_links_to_raw_trace`
- `test_digest_claim_without_pointer_is_blocking_finding`
- `test_scalar_only_justification_blocked` — trace-not-pass-rate.
- `test_evolve_agent_writes_only_inside_workspace`
- `test_edit_without_manifest_refused`
- `test_manifest_requires_all_four_fields`
- `test_symptom_as_root_cause_rejected`
- `test_root_cause_names_exactly_one_component`
- `test_every_manifest_gets_a_verdict_next_generation`
- `test_revert_verdict_triggers_git_revert`
- `test_partial_verdict_reopens_manifest`
- `test_orphaned_manifest_closed_stale`
- `test_staggered_iteration_dir_layout` — input/ + evolve/.
- `test_flips_attributed_to_prior_loop_manifests`
- `test_loop_terminates_on_target_pass_rate`
- `test_loop_terminates_on_max_iterations`
- `test_run_report_lists_manifests_verdicts_components`
- `test_minimal_start_two_components`
- `test_new_component_requires_root_cause_justification`
- `test_component_extrapolation_after_two_reverts`
- `test_layer_hop_recorded`
- `test_distill_returns_pointers_not_raw_bytes` — `#04` interaction.
- `test_constitution_edit_refused` — governance.
- `test_evolved_harness_not_auto_promoted`
- `test_manifest_schema_shared_with_dream_phase` — `#11` interaction.

## Edge cases

- **A manifest edits a component that a later `revert` removes.** The dependent manifest is closed `stale`, not
  silently kept; the failure it targeted re-enters the backlog for re-attribution.
- **Two manifests in one generation touch the same component.** Allowed, but the verdict step attributes flips to
  the *pair*; if it can't disambiguate which edit caused a flip, both are marked `partial` and re-filed
  separately next generation (no false `keep`).
- **The evolve loop oscillates** (edit A reverted, edit B reverts A's revert, …). Detected as a flip-flop on the
  same component across 3 generations → component-extrapolation is forced and the oscillating layer is frozen for
  one generation.
- **A distilled digest disagrees with the raw trace it points to.** The pointer wins; the digest claim is dropped
  and a finding flags the distiller (the digest must never assert beyond raw).
- **Transfer regression** — an evolved harness helps on the evolve suite but hurts on a held-out benchmark. Caught
  only at operator-promotion review (transfer is an *observed* property, not auto-verified in v1); flagged as an
  open question for a held-out transfer gate.
- **The agent files a manifest whose `predicted_impact` lists no tasks.** Rejected — an unfalsifiable prediction
  is not a prediction.
- **`target_pass_rate` is reached but the last generation's edits are unadjudicated.** The loop runs one final
  `evaluate`-only generation to render verdicts before writing the report (no edit ships unverified).
- **Raw traces for a task are missing/corrupt.** That task is excluded from attribution with a warning; manifests
  that predicted it get `partial`, never `keep` on absent evidence.

## Open questions

- Should there be a **held-out transfer gate** (evaluate the evolved harness on an unseen benchmark/model before
  operator promotion) rather than relying on review to catch transfer regressions?
- Should `predicted_impact` support a **confidence** field, weighting how aggressively a failed prediction
  reverts?
- Is **two generations** the right extrapolation trigger, or should it scale with how costly the layer-hop is?
- Should the Agent Debugger's distillation be a **fixed pipeline** or itself an evolvable component (recursion all
  the way down — the distiller is part of the harness)?
- Where exactly does a **sub-agent definition** (component 6) end and an **orchestration concern** (`#10`) begin
  when the evolve loop wants to add a specialist?
- Should `keep` verdicts decay — i.e. periodically re-test long-kept edits in case a later component shift made
  them load-bearing-by-accident?

## Out of scope

- Any model fine-tuning / weight update (the premise is a frozen model).
- The scheduler firing the evolve loop (→ `#09`) and the Evolve Agent's spawn/concurrency/isolation (→ `#10`).
- Memory/skill *content* lifecycle — promotion gate, skill GC, wiki curation (→ `#11`; this spec shares its
  manifest schema, not its content rules).
- The verification surface + rubric the `evaluate` step calls (→ `#07`).
- Governance *enforcement* of the `workspace/` boundary + constitution protection + the harness-path auto-merge
  block (→ `#13`).
- A port-the-harness-to-another-repo tool (transfer is observed, not productised in v1).
- Auto-promotion of an evolved harness to production without operator review.
