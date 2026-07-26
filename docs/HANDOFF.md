# Context Handoff: `dream` SDK (Arceus-Inc/Harness)

A head-start primer for a fresh session picking up implementation of `dream`.

## What this repo is

`dream` is the runtime layer of the Arceus stack: a pure-Python (3.11+) SDK for
building autonomous agent harnesses. It owns the agent loop, tools, hooks,
sandboxes, providers, and sessions. It knows **nothing** about employees,
companies, channels, or strategy — those live in sibling repos `chorus`,
`lattice`, and `horizon`, which depend only on `dream.contracts` (zero runtime
deps).

## Current state (2026)

- `src/dream/` is a **mature SDK** (~90 modules): harness, tools, hooks, tasks, contracts, etc.
- **2366+ tests**, strict mypy, CI on Python 3.11–3.13.
- `docs/specs/divo/` holds the design authority (specs 00–15).
- Consumer docs live in `consumer-facing-api/` and `examples/`.
- For a fresh agent session, read [AGENTS.md](../AGENTS.md) and [README.md](../README.md) first.

## Non-negotiable design rules (from README)

1. One facade: `Harness`. Many instances per process. **No globals.**
2. Constructor injection. Env / file loading is an opt-in helper (`dream.config`).
3. Async-first; sync facade in `dream.sync` is thin.
4. All consumer output is a typed `events.Event` — **no prints, no logging side
   effects.**
5. The public API is exactly what `dream/__init__.py` re-exports. Pinned by
   `tests/test_public_api.py`. Anything not re-exported may change.
6. Cross-repo contracts live in `dream.contracts` as `Protocol`s with **zero
   runtime dependencies**, so `chorus` / `lattice` / `horizon` can depend on
   them without pulling in providers.

## Specs (design authority) — two tracks in `docs/specs/`

- **`docs/specs/divo/`** — build-order sequence 00–14 (implementation specs:
  what you build, in order).
- **`docs/specs/pranjal/`** — conceptual new-specs 01–14. **Source of truth** to
  enrich.
- **Rule:** pranjal/new-specs = source of truth. OpenHarness (HKUDS) = reference
  only, never authority.
- `docs/ideation/` = `dream_harness.md`, `dream_harness_synthesis.md`,
  `self_improving.md`.
- `docs/existing_frameworks/` = 5 inspiration notes (voyager, hermes-agent,
  nanobot, openclaw, paperclip).

## Module → spec map (start order)

| Spec (divo) | `src/dream/` module |
|---|---|
| 01 repo & filesystem | `state/`, `utils/fs.py`, `config/paths.py` |
| 02 config & providers | `config/`, `api/` (anthropic, openai, `_registry`, `_pricing`) |
| 03 engine turn loop | `engine/` (`_loop`, `_engine`, `_cost`, `_messages`), `session.py`, `events.py` |
| 04 context engineering | `services/compact/`, `services/token_estimation.py`, `prompts/` |
| 05 tools & action space | `tools/` (`_base`, `_registry`, `builtin/*`) |
| 06 skills & MCP | `skills/`, `mcp/` |
| 07 task engine & cron | `tasks/`, `services/cron.py`, `services/exec_plan.py` |
| 08 claim / recovery / liveness | `state/store.py`, `utils/file_lock.py`, `services/session_storage.py` |
| 10 orchestration & swarm | `swarm/` (mailbox, registry, worktree, in_process, subprocess) |
| 11 memory & self-evolution | `memory/` |
| 13 sandbox / governance / hooks / plugins | `sandbox/`, `permissions/`, `hooks/`, `plugins/` |

## Key design decisions baked into specs

- **Hooks are observer-only** — never veto (diverges from OpenHarness's
  `block_on_failure`).
- **4 sandbox tiers**, default repo-write; subprocess backend v1, Docker is an
  upgrade path not a dependency (`sandbox/subprocess_backend.py` vs
  `docker_backend.py`).
- **Permissions:** `PermissionChecker` evaluation order + always-on
  credential-path guard (`permissions/_checker.py`, `_path_validator.py`).
- **Spec 11 vs 14:** 11 = *what the agent knows* (memory/skills, dream phase,
  promotion gate); 14 = *how the harness is built* (evolve loop, Change Manifest
  falsification).

## Env quirks

- zsh cwd resets between Bash calls → use absolute paths or `cd <path> &&`.

## Suggested first move

Read `docs/specs/divo/00-architecture-and-build-order.md`, then implement in spec
order 01 → 03 first (filesystem → config/providers → engine loop), TDD against
the mirrored `tests/` tree, keeping `tests/test_public_api.py` green.
