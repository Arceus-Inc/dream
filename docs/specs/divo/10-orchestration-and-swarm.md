# 10 — Orchestration, Swarm & Bridge

**One-liner:** A long task is not one undifferentiated stream of LLM calls — it is a small graph of
*roles* with explicit, file-mediated handoffs. A **planner** turns intent into a spec + ledger, a
**generator** does one sprint at a time, a conditional **evaluator** gates each sprint via a
negotiated contract, and every subagent talks to every other subagent through the repo — never an
in-memory bus. This spec defines the three roles, the sprint contract, capability minimisation, the
depth cap, the concurrency floor, and the swarm *substrate* (teams, teammate spawn, the leader's
notification inbox) that carries them — plus the **bridge** seam for the deferred remote-execution
case.

**Sources (source of truth):** `docs/specs/new-specs/06-orchestration-and-subagents.md` — the three
canonical roles (`planner` / `generator` / `evaluator`), planner-runs-once producing the Markdown
spec + JSON ledger under `docs/exec-plans/active/`, the **sprint contract** (`goal` / `scope_includes`
/ `scope_excludes` / `acceptance_criteria` (MUST/SHOULD) / `verification_steps` / `evaluator_enabled`
/ `negotiation_log`) written *before* any sprint code, the conditional evaluator (disable-able at task
and sprint level; reviewer-loop in `#03` is a *separate* always-on mechanism), the
`pass | needs-changes | fail` outcome→ledger mapping (`done` / `in_progress` / `blocked` + tech-debt
entry), **repo-only communication** (decision #9 — no in-memory message bus; handoffs are committed
files + jsonl events), the **subagent depth cap at 3** (runner→role→reviewer→remediation; depth 4
refused), **capability minimisation** (each role starts with its declared *minimum* tool/MCP set, no
mid-session expansion, escalation only via a `request_capability` event handled by the parent), the
concurrency rule (≤1 generator + ≤1 evaluator per task; parallel tasks isolated by worktree), and the
full acceptance criteria / Gherkin / `test_*` set are carried forward verbatim-in-substance and
enriched here. That spec's lineage ([ANT-2] orchestrator-worker, [OAI] evaluator-optimiser, taxonomy
§8) is the conceptual authority. · `#03` (the session FSM each role runs as; the reviewer loop lives
there) · `#02` (the per-role worktree; parallel-task isolation) · `#05` (the tool action space a
role's minimum set is drawn from; `is_read_only` gating for planner/evaluator) · `#07` (a spawned
subagent *is* a `TaskType` — `local_agent` / `remote_agent` / `in_process_teammate`; the completion
listener is the durable↔ephemeral seam that delivers a worker's result back to the leader) · `#08`
(parallel tasks each claim their own worktree under the two-lock store; the ≤1-generator floor is the
single-runner-per-task guard) · `#12` (the evaluator's rubric + critic isolation; the
`verification_steps` a contract names) · `#13` (the leader↔worker permission round-trip rides the
PermissionChecker + hook bus; sandbox tier bounds each role's minimum set).
**Reference (grounding only, not authority):** [openharness] —
`coordinator/coordinator_mode.py` (`TeamRegistry` / `TeamRecord` (name/description/agents/messages),
`TaskNotification` (task_id/status/summary/result/usage), `WorkerConfig`, `is_coordinator_mode`
(env `CLAUDE_CODE_COORDINATOR_MODE`), `format_task_notification` / `parse_task_notification` (worker
results delivered to the leader as `<task-notification>` XML carried in *user-role* messages),
`get_coordinator_system_prompt` (the leader's discipline: "every message you send is to the user;
worker results and system notifications are internal signals, never conversation partners")),
`coordinator/agent_definitions.py` (`AgentDefinition` pydantic model:
`name`/`description`/`system_prompt`/`tools` (None ⇒ all)/`disallowed_tools`/`skills`/`mcp_servers`/
`permission_mode` (default/acceptEdits/bypassPermissions/plan/dontAsk)/`isolation` (worktree/remote)/
`memory_scope`/`effort`/`color`; loaded layered from the config dir as YAML — the concrete shape of a
"role manifest"), `swarm/types.py` (`BackendType` = `subprocess|in_process|tmux|iterm2`, `PaneBackend`
protocol, `TeammateIdentity` (`agent_id` = `agentName@teamName`), `TeammateSpawnConfig`
(name/team/prompt/cwd/parent_session_id/model/system_prompt(+mode)/permissions/`plan_mode_required`/
`allow_permission_prompts`/`worktree_path`/`subscriptions`/`task_type`), `SpawnResult`
(task_id/agent_id/backend_type/success/error/pane_id), `TeammateMessage`, `TeammateExecutor` protocol
(`spawn`/`send_message`/`shutdown`)), `swarm/registry.py` (backend auto-detection — tmux via `$TMUX`,
iTerm2 via `$ITERM_SESSION_ID`, fall back to `in_process`/`subprocess`), `swarm/mailbox.py`
(`MailboxMessage` (id/type/sender/recipient/payload/timestamp/read), `MessageType` =
`user_message|permission_request|permission_response|sandbox_permission_*|shutdown|idle_notification`,
one-JSON-file-per-message with atomic `.tmp`+`os.rename` writes, exclusive file lock — the concrete
file-message mechanism), `swarm/permission_sync.py` (leader-worker permission round-trip; file-based
`pending/{id}.json`→`resolved/{id}.json` *or* mailbox-based; worker writes request, leader resolves,
worker polls), `swarm/team_lifecycle.py` (persistent teams as `team.json`; `TeamMember` / `TeamFile` /
`AllowedPath` (paths every member may edit without asking) / `TeamLifecycleManager`;
`sanitize_name` / `sanitize_agent_name`), `tools/agent_tool.py` (`AgentTool` name=`agent` — spawns a
teammate via the backend registry + `TeammateSpawnConfig`, resolving the role from `subagent_type`
against `get_agent_definition`), `tools/team_create_tool.py` / `team_delete_tool.py` (team CRUD as
tools), `bridge/` (`BridgeConfig` (dir/machine_name/max_sessions/session_timeout_ms), `WorkData`
(type=session|healthcheck, id), `WorkSecret` (version/session_ingress_token/api_base_url),
`BridgeSessionManager` / `BridgeSessionRecord`, `spawn_session` / `SessionHandle`,
`encode/decode_work_secret`, `build_sdk_url` — the remote-machine session-execution layer). Used to
name the team, teammate, spawn-config, leader-inbox, permission-round-trip, backend, and remote-bridge
primitives concretely. **Two divergences are deliberate and called out below:** OpenHarness stores
teams + mailbox + permissions under a *global* `~/.openharness/teams/` home dir, but the conceptual
spec's repo-only rule wins — our equivalents live *inside the worktree* so the repo stays the message
log; and OpenHarness's roles are generic `AgentDefinition`s loaded from config, whereas our authority
fixes *three canonical roles* — generic agent definitions are the mechanism, the three roles are the
contract.

---

## Why this matters

`#03` defines the loop a *single* session runs. `#07` defines the durable plan + ledger and the
runtime task handle. `#09` defines what happens to a *card* from intake to merge. None of them answer
the question this spec owns: **when a task is too big for one session, how do you split it across
several without the split itself becoming the failure mode?**

The conceptual spec (new-spec 06) names the three ways a naive "one giant agent" run dies on
long-horizon work, and the orchestration pattern that survives each:

1. **Plan drift** — the agent re-invents the goal as it goes; halfway through, the goal is no longer
   what the operator asked for. → A **planner** runs *once* at the start and commits a spec + ledger
   the generator must execute against. The goal is frozen in a file, not re-derived each turn.
2. **Verification collapse** — the same context that wrote the code grades it, and scores itself
   green. → A separate **evaluator** with its *own* context and a read-only toolset renders the
   sprint verdict. The grader never wrote the code.
3. **Context bloat** — every concern fights for the same window; everything degrades together. → Each
   role runs as its own session (`#03`) in its own worktree (`#02`) with the *minimum* tools it needs.
   The planner's context never carries the generator's diff noise.

The deep architectural commitment is **decision #9: communication is repo-only.** There is no
in-memory message bus between subagents. A handoff is a *committed file* plus a *jsonl event* with
from-role, to-role, and an artefact pointer. This is what makes a multi-agent run inspectable and
reproducible — the repo *is* the message log; you can `git log` the conversation between agents. It
also means a crashed subagent loses nothing the next runner can't reconstruct from committed state
(`#10p5` recovery), because the contract and ledger are already on disk.

OpenHarness shipped the *substrate* this pattern needs and never abstracted — a `TeamRegistry`,
`TeammateSpawnConfig`, a leader that receives worker results as `<task-notification>` XML, a
file-per-message mailbox with atomic writes, a leader↔worker permission round-trip, pluggable
execution backends (subprocess / in-process / tmux pane / iTerm2 pane), and a `bridge` for running a
session on a *remote* machine. We borrow its primitive shapes and its hard-won discipline (the
leader's "every message is to the user; worker results are internal signals" rule is a genuine
production lesson), but we re-anchor *where the messages live*: in the worktree, under the
repo-as-record invariant, not a global home dir.

## Scope

**In:**
- The three canonical roles (planner / generator / evaluator), what each owns, its minimum toolset,
  and how it hands off.
- The sprint contract: shape, when it is written, the bounded negotiation, the `imposed` fallback.
- The outcome→ledger mapping (`pass`/`needs-changes`/`fail`) and the tech-debt escalation on `fail`.
- Repo-only communication: the handoff event shape, the in-worktree mailbox, the rule that no
  in-memory bus exists.
- Capability minimisation: deriving the minimum set, refusing mid-session expansion, the
  `request_capability` escalation.
- The subagent depth cap (3) and the concurrency floor (≤1 generator + ≤1 evaluator per task).
- The swarm substrate: team registry, teammate identity + spawn config, the leader's notification
  inbox, the permission round-trip, the execution-backend abstraction (subprocess / in-process /
  pane).
- The **bridge** seam: how a `remote_agent` task *would* be realised, documented and gated off in v1.

**Out:**
- The session FSM each role runs (→ `#03`); the reviewer loop at session close (→ `#03` — a thin
  specialisation of the evaluator pattern, governed there, *always on*, not subject to
  `evaluator_enabled`).
- The evaluation *rubric content and weights* (→ `#12`).
- Per-worktree isolation mechanics and the merge/PR-promotion strategy for concurrent tasks (→ `#02`,
  `#09`).
- Cron-spawned subagents (→ `#07`).
- The durable task ledger / runtime `TaskRecord` / completion-listener internals (→ `#07`); this spec
  *consumes* the listener as the seam that delivers a worker result to the leader.
- Multi-host distribution as a *shipped v1 feature* — single-host in v1, like `#02`; the bridge is
  documented as the seam, not enabled.
- A graphical UI for subagent topology (deferred; the tmux/iTerm2 pane backends are referenced as the
  visual substrate but rendering is out of scope here).

## Key decisions (assumed defaults)

1. **Three canonical roles:** `planner`, `generator`, `evaluator`. Each is an `AgentDefinition`-shaped
   *role manifest* (name / description / system_prompt / `tools` / `disallowed_tools` / skills /
   mcp_servers / permission_mode / isolation / memory_scope / effort / color) but the *set* is fixed —
   the harness does not invent new top-level roles per task. Generic agent definitions are the
   mechanism; the three roles are the contract.
2. **Planner runs exactly once per task,** at the very start, and produces *both*
   `docs/exec-plans/active/{task-id}.md` (narrative spec) and `docs/exec-plans/active/{task-id}.json`
   (ledger). Re-planning mid-task is not supported in v1 — that is a *new task* that consumes the
   prior plan's state.
3. **Generator works in sprints.** A sprint is the smallest deliverable unit — typically one ledger
   step / one feature. It transitions exactly one step out of `pending` per sprint.
4. **Before each sprint where the evaluator is enabled, generator + evaluator negotiate a sprint
   contract,** written to `docs/exec-plans/active/{task-id}-sprint-{n}.json` *before any sprint code
   is committed*. Negotiation is bounded to **≤ 3 rounds**.
5. **Evaluator is conditional, not mandatory.** Disable-able at task level (flag on the exec-plan
   JSON) and sprint level (flag on the contract). Default policy: **enable when task complexity is
   near or above the model's comfort zone; disable for routine work** — the heuristic is conservative
   (when in doubt, leave it on). The `#03` reviewer loop is a *different*, always-on, session-close
   mechanism and is **not** governed by `evaluator_enabled`.
6. **Outcome → ledger mapping is fixed:** `pass` → step `done`, loop continues; `needs-changes` →
   step stays `in_progress`, items carried into the next sprint contract's `negotiation_log`; `fail`
   → step `blocked` **and** a tech-debt entry appended to `docs/exec-plans/tech-debt-tracker.md`
   (`#07`). The evaluator renders judgement and stops — it does **not** negotiate after the verdict.
7. **Communication is repo-only.** No in-memory message bus between subagents. Every handoff is (a) a
   file committed to the worktree and (b) a jsonl event carrying `from`-role, `to`-role, and an
   artefact pointer (file path or ref). The leader's *notification inbox* (worker-result delivery)
   and the leader↔worker *permission round-trip* are realised as **file-per-message queues inside the
   worktree** (`.harness/swarm/…`), atomic `.tmp`+rename writes, never a global home dir and never RAM.
8. **Capability minimisation over capability granting.** Each role starts with the *smallest* tool /
   MCP set its manifest declares — derived from the role, not from `tier_required` defaults (`#05`).
   Planner: `read_file`, `git`, `bash --read-only`. Generator: the default set (`#05`) ∩ the session's
   sandbox tier (`#13`). Evaluator: `read_file`, `git`, `bash --read-only`, plus exactly the
   verification tools the contract names. **A role cannot expand its toolset mid-session** — it must
   emit a `request_capability` event; the *parent* decides whether to spawn a broader subagent.
9. **Subagent depth capped at 3.** Depth 0 = the top-level runner (operator's invocation); depth 1 =
   planner / generator / evaluator; depth 2 = reviewer (`#03`) or a verification subagent the
   evaluator spawns; depth 3 = a remediation subagent inside a reviewer. A spawn request at **depth 4
   is refused** with an `exceeded subagent depth` error recorded as an info event; the parent must
   inline the work instead.
10. **Concurrency floor:** at most **one generator + one evaluator per task** at a time; multiple
    tasks may run in parallel, **each in its own worktree** (`#02`) with its own claim (`#08`). The
    ≤1-generator rule is the per-task single-runner guard that the claim spec enforces with its lease.
11. **A spawned subagent is a typed task** (`#07` `TaskType`): `local_agent` (subprocess on this
    host), `in_process_teammate` (same process, shared event loop), or `remote_agent` (the **bridge**,
    deferred). The leader receives the worker's result through the task's **completion listener**
    (`#07`), surfaced as a `<task-notification>`-shaped record — *not* a synchronous return value.
12. **The leader treats worker results as internal signals, not conversation.** Worker notifications
    arrive shaped like user-role messages but are distinguished by their `task-notification` envelope;
    the leader never thanks or addresses a worker — every message the leader *emits* is to the
    operator. (OpenHarness's coordinator discipline, adopted verbatim.)
13. **Execution backend is auto-detected, overridable.** Default `subprocess` (or `in_process` for
    cheap teammates); pane backends (`tmux` / `iTerm2`) are selected only when the harness detects it
    is *running inside* one (`$TMUX` / `$ITERM_SESSION_ID`) and are purely a *visualisation* choice —
    they do not change the repo-only communication contract.
14. **The bridge is a documented seam, off in v1.** `remote_agent` spawns route through a
    `BridgeConfig` + `WorkSecret` to run a session on a remote machine. v1 is single-host (like `#02`);
    the bridge types exist so the seam is named and testable, but the runner refuses `remote_agent`
    spawns unless an operator explicitly enables the bridge.

## Artefact shapes

### Role manifest (`AgentDefinition`-shaped)

Per-role, layered from `.harness/roles/{role}.toml` (project) over bundled defaults:

```
name            : "planner" | "generator" | "evaluator"
description      : when this role is used (one line)
system_prompt    : the role's standing instructions
system_prompt_mode: "default" | "replace" | "append"
tools            : explicit allow-list  (null ⇒ "all" — only the generator may use null,
                    and even then it is intersected with the sandbox tier at #13)
disallowed_tools : explicit deny-list (planner/evaluator deny all writers)
skills           : skill names this role may invoke (#06)
mcp_servers      : MCP servers this role may reach (#06 allowlist still applies)
permission_mode  : "default" | "acceptEdits" | "plan" | "dontAsk"   (no bypassPermissions for v1 roles)
isolation        : "worktree" (default) | "remote" (bridge only)
memory_scope     : "user" | "project" | "local"
effort           : "low" | "medium" | "high"
color            : UI differentiation (pane border / notification tag)
```

The *minimum* toolset is computed as `tools ∩ tier(#13)` for the generator, and the fixed read-only
triplet (+ contract-named verifiers) for planner/evaluator — never widened from this manifest at
runtime.

### Sprint contract (`docs/exec-plans/active/{task-id}-sprint-{n}.json`)

```
task_id          : string
sprint_number    : int
goal             : one sentence
scope_includes   : [bullet, …]
scope_excludes   : [bullet, …]
acceptance_criteria : [ "MUST …", "SHOULD …", … ]
verification_steps  : ordered [ { kind: "test"|"lint"|"eval", ref } ]   (#12 / #07)
evaluator_enabled : bool
imposed          : bool        (true ⇒ negotiation hit the 3-round cap; evaluator's last proposal taken)
negotiation_log  : append-only [ { ts, from, to, message } ]
```

Written **before** any source file in the worktree is modified for that sprint (acceptance criterion
#7). The `negotiation_log` is the durable record of how the contract was reached — including
disagreement when `imposed: true`.

### Handoff event (jsonl, on the session/run stream)

```
type      : "handoff.{from}_to_{to}"   e.g. "handoff.planner_to_generator"
ts        : iso8601
from_role : "planner" | "generator" | "evaluator" | "runner"
to_role   : "generator" | "evaluator" | "reviewer" | …
artefacts : [ { kind: "spec"|"ledger"|"contract"|"eval"|"diff", path | ref } ]   (≥1 pointer)
```

Every cross-role transition emits exactly one such event; the next session locates its inputs *only*
from these pointers (repo-only rule).

### Worker notification (the completion-listener payload → leader inbox)

`TaskNotification`-shaped, written as one JSON file into the leader's in-worktree inbox
(`.harness/swarm/{leader}/inbox/{ts}_{id}.json`, atomic `.tmp`+rename):

```
task_id : string
status  : "completed" | "failed" | "killed"
summary : one line
result  : optional pointer/body
usage   : optional { input_tokens, output_tokens, cost_usd }
```

The leader polls its inbox between turns; each unread notification becomes a `<task-notification>`
envelope in the leader's context (decision #12), not a conversational message.

### Permission round-trip (`.harness/swarm/{team}/permissions/`)

Worker writes `pending/{id}.json` (the tool + args it wants approved); leader reads pending, decides
via the `PermissionChecker` (`#13`), writes `resolved/{id}.json` (`allow` / `deny` / `allow_once`);
worker polls for its `id`. File-based by default; the mailbox `permission_request` /
`permission_response` message types are the alternative transport for pane backends.

### Teammate spawn config (`TeammateSpawnConfig`-shaped, runtime, ephemeral)

```
name, team, prompt, cwd, parent_session_id
model?, system_prompt?(+mode)
permissions: [tool, …]          (the minimum set, decision #8)
plan_mode_required: bool
allow_permission_prompts: bool  (false ⇒ unlisted tools auto-denied — the minimisation enforcement)
worktree_path?                  (#02 — the isolated filesystem)
subscriptions: [topic, …]       (which handoff/notification topics this teammate watches)
task_type: "local_agent" | "in_process_teammate" | "remote_agent"   (#07)
```

This is the *runtime* spawn handle; the durable record is the ledger + contract on disk. `agent_id` =
`{sanitised name}@{sanitised team}`.

## Behaviours

### Task start (planner)

1. Runner allocates `task-id`, creates the worktree (`#02`), claims it (`#08`), starts a `planner`
   session (`#03`) with the read-only minimum set.
2. Planner reads intent + repo state + `core-beliefs.md` + product specs; drafts the narrative spec
   and the JSON ledger.
3. Planner commits both files into the worktree.
4. Session ends; runner emits `handoff.planner_to_generator` with pointers to *both* files. The
   planner emits exactly one `planner.run.completed`.

### Generator + evaluator loop (per sprint)

1. Runner starts a `generator` session in a fresh worktree built on the current state.
2. Generator picks the next `pending` ledger step.
3. **If the evaluator is enabled** for this task: runner spawns a short `evaluator` session whose only
   job is to *propose* acceptance criteria for the upcoming sprint. Generator and evaluator exchange
   proposals via the `negotiation_log` (≤ 3 rounds). The final contract is committed.
4. Generator executes the sprint and commits. (No source file is touched before the contract exists —
   criterion #7.)
5. **If the evaluator is enabled:** the evaluator runs the contract's `verification_steps`, then
   writes exactly one evaluation record under `docs/evals/{task-id}/sprint-{n}.json`
   (`pass | needs-changes | fail` + score + notes — rubric per `#12`).
6. Outcome → ledger (decision #6): `pass` → `done`, advance; `needs-changes` → stays `in_progress`,
   items into next contract's log; `fail` → `blocked` + tech-debt entry.

### Disabling the evaluator

- Task-level: a flag on the exec-plan JSON. Sprint-level: a flag on the sprint contract.
- When disabled, the generator skips negotiation entirely and proceeds directly to execution; **no**
  evaluation record is written.
- The `#03` reviewer loop still runs at session close regardless — it is a separate, lightweight,
  always-on mechanism.

### Capability minimisation & escalation

- The harness computes each role's minimum set at spawn from its manifest (decision #8) and passes it
  as the teammate's `permissions` with `allow_permission_prompts: false` — so any tool *not* in the
  set is auto-denied, not prompted.
- A mid-session attempt to call a tool outside the set returns `tool not in role manifest` (not a
  permission prompt — a hard refusal).
- The role's only recourse is to emit a `request_capability` event naming the tool + reason. The
  *parent* runner decides whether to spawn a new subagent with a broader (still minimal) set —
  capabilities are never widened in place.

### Worker result delivery (the leader loop)

1. A spawned worker is registered as a `#07` task with a **completion listener**.
2. On worker completion the listener writes a `TaskNotification` file into the leader's in-worktree
   inbox.
3. Between turns the leader drains its inbox; each unread notification is injected as a
   `<task-notification>` envelope (decision #12) — the leader summarises it *for the operator* and
   never replies *to the worker*.

### Permission round-trip (leader as approver)

1. A worker hits a tool that needs approval it cannot self-grant; it writes `pending/{id}.json`.
2. The leader reads pending requests, runs each through the `PermissionChecker` (`#13`), and writes
   `resolved/{id}.json`.
3. The worker polls for its `id` and proceeds (or refuses) per the resolution. All four files are
   committed/visible in the worktree — the decision trail is inspectable.

### Backend selection (visualisation only)

- The backend registry auto-detects: inside tmux (`$TMUX` + binary) → `tmux` panes; inside iTerm2
  (`$ITERM_SESSION_ID` + `it2`) → `iTerm2` panes; otherwise `subprocess` (or `in_process` for cheap
  teammates).
- Pane backends only affect *rendering* (one pane per teammate, leader pane, border colours). They do
  **not** change the repo-only communication contract — messages still flow through files, not the
  pane.

### The bridge (deferred remote execution)

- A `remote_agent` spawn would encode a `WorkSecret` (session ingress token + API base URL), hand a
  `BridgeConfig` (target machine, max sessions, timeout) to the remote `BridgeSessionManager`, and run
  the session there — its commits flow back via the shared git remote (`#01`/`#02`), its notifications
  via the same inbox shape over the wire.
- **In v1 the runner refuses `remote_agent` unless the bridge is explicitly enabled.** The types and
  the seam exist so the multi-host upgrade is a configuration change, not a redesign.

## Acceptance criteria

### Planner (MUST)

1. **MUST** run exactly once at task start.
2. **MUST** produce both the Markdown spec and the JSON ledger.
3. **MUST** be restricted to read-only access outside the exec-plan folder.
4. **MUST** emit a `handoff.planner_to_generator` event at end of run.

### Generator (MUST)

5. **MUST** pick the next `pending` ledger step at each sprint start.
6. **MUST** transition a step out of `pending` exactly once per sprint.
7. **MUST** record a sprint contract before executing any sprint where the evaluator is enabled — no
   source-file commit in the worktree may predate the contract.
8. **MUST** never write outside its worktree.

### Evaluator (MUST/SHOULD)

9. **MUST** participate in contract negotiation when enabled, bounded to ≤ 3 rounds.
10. **MUST** produce exactly one evaluation record per sprint when enabled.
11. **MUST NOT** negotiate after rendering judgement — its output is `pass | needs-changes | fail`
    with notes, full stop.
12. **SHOULD** be disabled by default for explicitly "routine" tasks; enabled otherwise.

### Concurrency & isolation (MUST)

13. **MUST** isolate parallel tasks via worktree (`#02`) + claim (`#08`).
14. **MUST** allow at most one generator and one evaluator per task at a time.
15. **MUST** refuse to spawn a subagent at depth 4 or deeper, recording the refusal as an info event.

### Capability minimisation (MUST)

16. **MUST** start each role with its declared minimum toolset (passed as `permissions` with
    `allow_permission_prompts: false`).
17. **MUST** refuse mid-session tool expansion with `tool not in role manifest`.
18. **MUST** allow escalation only via the `request_capability` event, handled by the parent.

### Repo-only communication (MUST)

19. **MUST** route all subagent handoffs through committed files plus jsonl events — no in-memory bus.
20. **MUST** record each handoff with from-role, to-role, and ≥1 artefact pointer.
21. **MUST** store the leader inbox and the permission round-trip as file-per-message queues *inside
    the worktree* (`.harness/swarm/…`), atomic-written — never a global home dir.

### Worker delivery & leader discipline (MUST)

22. **MUST** deliver a worker's result to the leader through the task's completion listener as a
    `TaskNotification`-shaped record, not a synchronous return.
23. **MUST** treat worker notifications as internal signals — the leader addresses only the operator,
    never the worker.

### Backend & bridge (MUST)

24. **MUST** keep the communication contract repo-only regardless of execution backend (subprocess /
    in-process / pane).
25. **MUST** refuse `remote_agent` spawns in v1 unless the bridge is explicitly enabled.

## Acceptance scenarios

```gherkin
Scenario: Planner runs once and produces both artefacts
  Given the operator starts a new task with intent text
  When the planner session completes
  Then docs/exec-plans/active/{task-id}.md exists with narrative content
  And docs/exec-plans/active/{task-id}.json exists with a ledger
  And the planner session jsonl shows exactly one planner.run.completed event.

Scenario: Sprint contract precedes generator code
  Given the evaluator is enabled for task T1
  When generator begins sprint 2
  Then a contract file docs/exec-plans/active/T1-sprint-2.json exists
  And it lists acceptance_criteria and verification_steps
  And no commit modifying source files in T1's worktree predates the contract.

Scenario: Evaluator can be disabled at task level
  Given the exec-plan JSON sets evaluator_enabled = false
  When generator begins a sprint
  Then no contract negotiation occurs
  And generator proceeds directly to execution
  And no evaluation record is written.

Scenario: Sprint pass marks step done and loop continues
  Given evaluator returns "pass" for sprint 3
  When the runner records the evaluation
  Then the ledger step for sprint 3 moves to "done"
  And generator picks the next pending step in the next sprint.

Scenario: Sprint needs-changes keeps step in progress
  Given evaluator returns "needs-changes" with two items
  When the runner records the evaluation
  Then the step remains in_progress
  And the items appear in the next sprint contract's negotiation_log.

Scenario: Sprint fail marks step blocked and files tech debt
  Given evaluator returns "fail" with a reason
  When the runner records the evaluation
  Then the step transitions to "blocked"
  And a tech-debt entry is appended to docs/exec-plans/tech-debt-tracker.md.

Scenario: Negotiation hits the cap and the evaluator's proposal is imposed
  Given generator and evaluator have exchanged 3 rounds without agreement
  When the runner closes negotiation
  Then the committed contract uses the evaluator's last proposal
  And the contract has imposed = true
  And a warning event records the disagreement.

Scenario: Subagent depth capped at 3
  Given a reviewer at depth 2 spawns a remediation subagent at depth 3
  And that subagent attempts to spawn another subagent
  When the depth-4 spawn is requested
  Then the runner refuses with an "exceeded subagent depth" error
  And the request is recorded as an info event.

Scenario: Mid-session tool expansion refused
  Given a planner running with the read-only toolset
  When the planner attempts to call write_file
  Then the call returns "tool not in role manifest"
  And the planner can emit a request_capability event instead.

Scenario: Capability escalation handled by the parent, not in place
  Given a generator emits request_capability for an MCP tool it lacks
  When the parent runner handles the event
  Then the generator's own toolset is unchanged
  And the parent may spawn a new subagent with the broader minimum set.

Scenario: Parallel tasks isolated via worktrees and claims
  Given two tasks T1 and T2 running concurrently
  When each runs a generator session
  Then the two generators operate in different worktrees (#02)
  And each holds its own claim (#08)
  And neither sees the other's uncommitted state.

Scenario: Handoff event records artefact pointer
  Given planner has just completed
  When the runner emits handoff.planner_to_generator
  Then the event payload includes the spec path and ledger path
  And the next generator session can locate both files from the payload.

Scenario: Worker result reaches the leader as a notification, not a return
  Given the leader spawned a worker as a local_agent task
  When the worker completes
  Then a TaskNotification file appears in the leader's in-worktree inbox
  And the leader injects it as a <task-notification> envelope on its next turn
  And the leader's reply addresses the operator, not the worker.

Scenario: Leader inbox lives in the worktree, not a global home dir
  Given a leader with one pending worker
  When the worker writes its completion notification
  Then the notification file is under the worktree's .harness/swarm path
  And no file is written under ~/.openharness.

Scenario: Permission round-trip is file-mediated and inspectable
  Given a worker requests approval for a write outside its allowed paths
  When the leader resolves the request
  Then pending/{id}.json and resolved/{id}.json both exist in the worktree
  And the worker proceeds only after reading the resolution.

Scenario: Pane backend does not change the communication contract
  Given the harness is running inside tmux and spawns a teammate in a pane
  When the teammate produces a handoff
  Then the handoff is still a committed file plus a jsonl event
  And nothing is communicated through the pane itself.

Scenario: Remote agent refused without the bridge enabled
  Given the bridge is not enabled
  When a spawn requests task_type = remote_agent
  Then the runner refuses the spawn
  And records why (bridge disabled in v1).
```

## Tests

- `test_planner_runs_once_at_task_start`
- `test_planner_produces_spec_and_ledger`
- `test_planner_restricted_to_read_only_outside_exec_plans`
- `test_planner_emits_handoff_event`
- `test_generator_picks_next_pending_step`
- `test_generator_transitions_step_exactly_once_per_sprint`
- `test_sprint_contract_written_before_sprint_code`
- `test_generator_cannot_write_outside_worktree`
- `test_evaluator_negotiation_bounded_at_3_rounds`
- `test_evaluator_writes_exactly_one_evaluation_record`
- `test_evaluator_does_not_negotiate_after_judgement`
- `test_evaluator_can_be_disabled_at_task_level`
- `test_evaluator_can_be_disabled_at_sprint_level`
- `test_disabled_evaluator_skips_negotiation_and_record`
- `test_pass_outcome_marks_step_done`
- `test_needs_changes_keeps_step_in_progress`
- `test_fail_outcome_marks_step_blocked`
- `test_fail_outcome_files_tech_debt_entry`
- `test_negotiation_cap_imposes_evaluator_proposal` — `imposed: true` + warning event.
- `test_subagent_depth_capped_at_3`
- `test_subagent_depth_violation_emits_info_event`
- `test_role_starts_with_minimum_toolset`
- `test_role_spawned_with_allow_permission_prompts_false` — unlisted tools auto-denied.
- `test_mid_session_tool_expansion_refused`
- `test_request_capability_event_recordable`
- `test_request_capability_does_not_widen_role_in_place`
- `test_parallel_tasks_use_separate_worktrees`
- `test_parallel_tasks_hold_separate_claims` — `#08` interaction.
- `test_at_most_one_generator_per_task`
- `test_at_most_one_evaluator_per_task`
- `test_handoff_event_carries_artefact_pointer`
- `test_no_inmem_messaging_between_subagents`
- `test_worker_result_delivered_via_completion_listener` — `#07` seam.
- `test_worker_notification_is_task_notification_shaped`
- `test_leader_addresses_operator_not_worker`
- `test_leader_inbox_lives_in_worktree_not_home_dir` — the location divergence.
- `test_permission_roundtrip_files_present_and_committed`
- `test_pane_backend_does_not_alter_communication_contract`
- `test_backend_autodetect_tmux_when_inside_tmux`
- `test_backend_autodetect_falls_back_to_subprocess`
- `test_remote_agent_refused_without_bridge`
- `test_agent_id_is_sanitised_name_at_team`

## Edge cases

- **Operator wants to re-plan mid-task.** Not supported in v1; the operator opens a *new task* whose
  exec-plan supersedes the prior one. Keeps the planner-runs-once rule simple.
- **Generator and evaluator can't agree after 3 rounds.** Runner takes the *evaluator's* last
  proposal, commits the contract with `imposed: true`, and emits a warning event; the
  `negotiation_log` preserves the disagreement for later review.
- **A step's verification needs a tool the evaluator's minimum set lacks.** Contract negotiation may
  expand the evaluator's toolset *for that sprint only* (the verifiers named in `verification_steps`);
  the expansion is scoped to the contract, never persisted to the role manifest.
- **Evaluator becomes flaky** (frequent `fail` on actually-correct sprints). Detected by the eval
  tuning loop (`#12`), which patches the evaluator's prompt — not handled here.
- **A subagent crashes mid-sprint.** The contract + ledger are already committed; the next runner
  invocation resumes from the last checkpoint (`#02`/`#10p5`) with the contract intact — nothing in RAM
  was load-bearing.
- **Leader inbox grows unbounded** (many completed workers, leader idle). Read notifications are
  marked `read` and may be compacted; the *handoff jsonl* remains the durable record. Inbox is a
  delivery buffer, not the system of record.
- **Two concurrent tasks touch the same file via separate worktrees.** Resolved at
  merge/PR-promotion time per task policy (`#09`); orchestration does not define the merge strategy.
- **Pane backend unavailable** (operator not inside tmux/iTerm2). The registry falls back to
  `subprocess`/`in_process`; no visualisation, identical communication contract.
- **Bridge enabled but remote machine unreachable.** The `remote_agent` spawn fails as a normal task
  failure (`#07`) → `failed` notification → the leader escalates; no special-casing here.

## Open questions

- Whether the depth cap should be 3 or 4 (current default 3; review after the first month of real
  use — same open question as new-spec 06).
- Whether to allow a "second evaluator" pattern (two rubrics, operator tie-break).
- Whether contract negotiation should be capped *below* 3 rounds for routine sprints.
- Whether the leader inbox should be pruned automatically or left for the memory GC (`#11`).
- Whether `in_process_teammate` and `subprocess` should share one minimum-set enforcement path or
  diverge (in-process teammates share the event loop — tighter coupling, easier leakage).

## Out of scope

- The session FSM and the always-on reviewer loop at session close (→ `#03`).
- Evaluation rubric content and weights (→ `#12`).
- Per-worktree isolation mechanics and the concurrent-task merge strategy (→ `#02`, `#09`).
- Cron-spawned subagents (→ `#07`).
- Completion-listener / `TaskRecord` / durable-ledger internals (→ `#07`).
- Multi-host distribution as a shipped feature — single-host v1; the bridge is the named seam only.
- A graphical UI for subagent topology (pane backends are referenced as the visual substrate;
  rendering is deferred).
