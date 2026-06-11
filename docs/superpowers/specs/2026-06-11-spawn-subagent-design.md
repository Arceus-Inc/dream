# spawn_subagent: runtime-scoped child sessions (v1)

Date: 2026-06-11. Status: approved (brainstormed in-session; decisions below
are the user's).

## Problem

dream has no way for a running session to delegate: the generator cannot
fan work out inside a sprint, and a bare "manager" session cannot dispatch
scoped workers. The reference pattern (OpenAI Agents SDK `spawn_subagent`)
shows the right shape: recursion as a *tool*, children assembled at runtime
from the caller's arguments, capability narrowing at spawn time, bounded
depth, failure-as-data.

## Decisions (user-selected)

- **One mechanism, pure harness** — same tool serves generator fan-out and
  manager dispatch; nothing employee-specific lives in dream.
- **Inline only** — the parent's tool call blocks until the child session
  finishes; the child's final text is the tool result. Background mode is a
  future addition (via the existing BackgroundTaskManager seam).
- **Same workspace** — the child shares `working_dir` and the sandbox tier;
  artifacts land where the parent reads them. Safe because inline children
  are sequential. (Note: `RoleManifest.isolation` exists but is unwired
  today; worktree isolation arrives with background mode.)
- **Depth 1 — children are leaves** — only top-level sessions get the tool;
  a child never spawns. No depth counters needed.
- **Nothing hardcoded** — the child agent does not exist anywhere in code or
  config; it is constructed per call from the parent model's runtime
  arguments (task, tools, model, max_turns) and discarded after. Only
  mechanism is fixed: the generic framing template, the guardrails (spawn
  stripped from children, cap, tier/trust intersection), and defaults.
- **Task = user message** — `run_role(manifest, task)` delivers the task as
  the child's opening user message. The manifest's `system_prompt` carries
  only the *static* subagent framing ("you are a scoped subagent; your final
  message is returned verbatim to the caller as the tool result — make it
  the deliverable"). Static framing keeps the system-prompt prefix
  prompt-cache friendly across spawns.

## Design

### 1. Architecture & data flow

New package `dream/spawn/` following the existing per-surface session-context
convention (`skills/_session.py`, `sandbox/_session.py`, `memory/_context.py`):

- `SPAWN_CONTEXT_KEY = "spawn_context"`.
- `SpawnContext` carrying: the async `spawn` closure, a per-session
  `SpawnBudget` (mutable counter object with `acquire() -> bool`, cap 16),
  an optional `emit` callable (observer bridge), and a
  `fire_subagent_stop` callable (hook bridge).
- `read_spawn_context(metadata)` helper.

The closure is built in `_factory.py` (which already closes over the
harness for the hooks executor) and does:

```
spawn(task, tools, model, max_turns)
  → manifest = RoleManifest(
        name="subagent",
        description="Runtime-scoped child session.",
        tools=tuple(requested) if requested else None,   # None = all (minus disallowed)
        disallowed_tools=("spawn_subagent",),            # depth-1 star
        system_prompt=SUBAGENT_FRAMING)                  # static, generic
  → result = await harness.run_role(manifest, task,
        options=SessionOptions(model=model, max_turns=max_turns),
        observer=<parent's observer, when known>)
  → return SpawnOutcome(final_text=result.final_text,
                        session_id=result.session_id, cost=result.cost)
```

`RoleName` literal gains `"subagent"`. By-name resolution
(`default_role_manifest("subagent")` / `load_role_manifest`) raises a clear
error — subagent manifests are always synthesized, never loaded.

**Stash rule:** `_build_session_engine` stashes a fresh `SpawnContext` into
`context_metadata` unless the session's role manifest
(`options.metadata[ROLE_MANIFEST_METADATA_KEY]`) has `name == "subagent"`
or `build_harness(spawn=False)`. Belt-and-braces with `disallowed_tools`.

**Observer bridge:** `run_role` stamps its `observer` into
`SessionOptions.metadata` (new key beside the manifest key). The factory
reads it: `SpawnContext.emit = observer.on_event` when present. The closure
emits `spawn.started` / `spawn.completed`
`{parent_session_id, child_session_id?, status}` and passes the same
observer to the child's `run_role`, so the child streams live into the
parent's transcript.

**Attenuation** is the existing stack: synthesized manifest ∩ sandbox tier ∩
per-tool trust (`compute_session_role_allowlist` via the engine factory). A
child can never exceed the workspace envelope. Planner/evaluator manifests
(read-only triplet) never include the tool; the generator (`tools=None`) and
bare sessions pick it up from the default registry.

### 2. Tool surface

`SpawnSubagentTool(BaseTool)` — `src/dream/tools/builtin/spawn_subagent.py`:
name `spawn_subagent`, risk `mutating`, tier 1, timeout 600s.

Input: `task: str` (required); `tools: list[str] | None = None`
(None = inherit everything permitted); `model: str | None = None`
(None = parent's model); `max_turns: int | None = None`.

Output `ToolResult`: child's final text as `content`;
`structured = {"status": "completed" | "failed", "child_session_id": ...,
"cost_usd": ...}`. Unknown requested tool names are reported in the result
(`structured["unknown_tools"]`), not silently dropped.

Default registry: 18 → 19 tools (pin tests updated).

### 3. Bounds & errors

- **Cap**: `MAX_SPAWNS_PER_SESSION = 16` (module constant); the 17th call
  returns the three-part error (`root_cause`: spawn cap reached;
  `safe_retry`: consolidate remaining work into fewer subtasks;
  `stop_condition`: do not spawn again this session).
- **Child failure** (engine error, max-turns exhaustion): caught in the
  tool; returns `status: "failed"` with the error as content — never raises
  through the parent's turn.
- **No spawn context** (child session, or `spawn=False`): graceful
  three-part "spawning unavailable" error.

### 4. Observability

- `spawn.started` / `spawn.completed` observer events (see bridge above).
- The dormant `HookEvent.SUBAGENT_STOP` finally fires: through the parent
  session's hook executor when a child completes, payload
  `{child_session_id, status}`. The factory passes the per-session executor
  into `SpawnContext.fire_subagent_stop`.
- Child sessions write their own JSONL traces; ids link parent ↔ child.

### 5. `build_harness` change

One new param: `spawn: bool = True` (consistent with
`skills/memory/mcp/plugins`). No change to `run_task`'s signature.

### 6. Testing

TDD unit suite: cap enforcement (17th refused); attenuation (requested ∩
registry; spawn stripped from the child's wire schema); no-context graceful
error; failure envelope (child error → status failed, parent turn survives);
SUBAGENT_STOP fired with payload; `spawn=False` omits context + tool;
observer bridge (events emitted; child streams to parent's observer);
unknown tool names reported; by-name subagent resolution raises.

Live e2e (standing 5–6 scenario rule, `scripts/e2e_spawn.py`): a parent task
that must delegate (e.g. one subagent per file to summarize 3 files, then
merge) — oracles: `tool→ spawn_subagent` dispatches in the trace; child
artifacts on disk; the child never dispatches spawn itself; control run with
`spawn=False` never dispatches it.
