# Bounded Depth-2 Subagent Spawning — Design

**Status:** pre-implementation (TDD)
**Motivation:** v1 subagents are flat (depth-1): a subagent cannot spawn a subagent. The chorus
Marketer's Strategist needs to spawn the shared Web-Research Orchestrator mid-beat. Lift the cap to
a **bounded depth of 2**, safe by construction.

## What blocks it today (traced)

1. `_inline_executor._build_subagent_manifest` hardcodes `disallowed_tools=("spawn_subagent",)` on
   every child — the hard block.
2. The child session's subagent set + spawn counter are wired by `_build_session_engine`
   (`_factory.py`) from **harness-level** state, and it **ignores arbitrary `options.metadata`
   keys** (it reads only the role-manifest key). So even with the tool allowed, a child can't be
   handed a *scoped* set or a *shared* counter through `run_role(options=…)` today.

## Design — additive, backward-compatible

### Data (`_declaration.py`)
- `Subagent.spawnable: tuple[Subagent, ...] = ()` — the exact Tier-2 agents THIS subagent may
  dispatch. Empty (default) = leaf; behavior unchanged. Round-trips in `to_dict`/`from_dict`.
- `MAX_SUBAGENT_DEPTH = 2` module constant. A child may spawn only if `agent.depth <
  MAX_SUBAGENT_DEPTH` **and** `agent.spawnable` is non-empty. Grandchildren (depth 2) are leaves.

### Factory prefers incoming spawn context (`_build_session_engine`)
When `options.metadata` carries the spawn keys, prefer them over the fresh harness defaults:
- `SUBAGENT_SET_CONTEXT_KEY` — the scoped child set (so a child spawns only what it declared, not
  the whole parent roster).
- `SPAWN_COUNT_KEY` — the parent's counter object, so the per-beat cap (`MAX_SPAWNS_PER_BEAT=10`)
  spans the **whole tree** (total spawns across all depths ≤ 10) instead of resetting per child.
This is the one core change; it's guarded so the parent (top) path is byte-identical to today.

### Inline executor seeds the child (`run_subagent_inline` / `_build_subagent_manifest`)
When the child is spawn-eligible:
- build a child `SubagentSet` from `agent.spawnable`, each intersected with the child's effective
  tools (grandchild ⊆ child ⊆ parent stays transitive),
- keep `spawn_subagent` in the child's tools (drop it from the disallow list) — only for eligible
  children; leaves stay disallowed exactly as today,
- pass through `SessionOptions.metadata`: the scoped set, the **same** counter object, harness,
  tracer, and the child's parent-tools.

`_output_guard` stays flat — a reformat pass never spawns.

## Safety properties
- Depth hard-capped at 2 (constant, enforced in the executor).
- A subagent spawns only what it explicitly declares in `spawnable` — never the parent's full set.
- Tool intersection is transitive: a grandchild can only ever narrow.
- One shared per-beat counter caps total spawns across the tree (cost backstop).
- Serial join, in-process, lease/timeout unchanged.

## Slices (TDD)
1. `Subagent.spawnable` field + round-trip; factory prefers incoming `SUBAGENT_SET`/`SPAWN_COUNT`
   from `options.metadata` (the enabling change) — unit test proves a custom set/counter in
   `options.metadata` reaches the child tool `ctx.metadata`.
2. Depth cap + eligibility in `_inline_executor`: eligible child keeps `spawn_subagent` + gets its
   scoped set; leaf / at-cap child stays disallowed.
3. Shared counter spans the tree: a nested spawn tree that would exceed 10 if counters were
   per-session is capped at 10 total.
4. chorus side: `SubagentSpec.spawnable` + projection maps nested specs → dream `Subagent.spawnable`;
   full gate.
