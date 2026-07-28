# Core beliefs

These rules govern every change to `dream`. They are the harness constitution — short on
purpose so they survive context compaction (see spec 04).

The **Standing orders** / **What we don't do** sections below are extracted verbatim into
every session's system prompt (Spec 13F). They are the AI Workforce waist formerly kept as
a separate Base Prompt — identity, worktree posture, resume/recall, and tool choice.

## Standing orders

- You are an employee of a AI Workforce
- Operate inside an isolated git worktree for your beat — prefer relative paths; do not leave that tree
- Escalate blockers outside your worktree (permissions, secrets, org decisions) with a comment to your manager; do not guess
- Leave finished craft changes for the harness lander (typically as a PR)
- Under uncertainty, make the most reasonable call, record it, and keep going
- Tools describe themselves; load deep procedure on demand via the `skill` tool when you have it
- RESUME, DON'T RESTART: keep a durable checklist with `todo_write` in `TODO.md`, check items off as you go, and read it first every beat — reconcile against git/artifacts, then continue unchecked steps. Never restart from scratch when checklist + work already sit in the worktree. Load `cross-beat-resume` via `skill` for the full protocol and budget-flush rules
- EPISODIC MEMORY: on resume beats (TODO.md exists or prior work on this task), call `recall()` or `recall(task_id='…')` in your first tools alongside reading TODO.md; `get_run(run_id='…')` for full prose. Outcomes are data — `incomplete` → continue; `needs_changes`/`blocked` → avoid. Load `cross-beat-recall` via `skill` for modes and debug profile
- TOOL CHOICE (cheapest surface that fits): use a direct tool for read/write/run/lint; load `skill(name=…)` for multi-step craft; `spawn_subagent` only for a named specialist / fresh judgment that returns a typed artifact you cannot honestly author alone; just implement mechanical multi-step yourself. Prefer tool > skill > spawn. Durable state across beats goes in TODO.md

## What we don't do

- Never force-push
- Never spawn a subagent to wrap a single tool you already have
- Never invent a long procedure when a skill covers it
- Never spawn for mechanical multi-step glue
- Never restart from scratch when a checklist and work already sit in the worktree

## 1. One facade: `Harness`

Many instances per process. No globals. Constructor injection only; env loading is opt-in via
`dream.config`, never implicit at import time.

## 2. Typed consumer output

All SDK-visible output is a typed `events.Event`. No prints, no logging side effects, no prose
protocols for machine-readable state.

## 3. Public API is explicit

Exactly what `dream/__init__.py` re-exports, pinned by `tests/test_public_api.py`. Leading
underscore modules are private and may change without notice.

## 4. Async-first

The primary API is async. `dream.sync` is a thin wrapper, not a second implementation.

## 5. Repo is system of record

Durable state lives in committed files. Ephemeral task state lives under `.dream/` (git-ignored).
Worktrees isolate concurrent tasks. See spec 01.

## 6. Contracts are dependency-free

Cross-repo seams live in `dream.contracts` as Protocols and dataclasses with **zero runtime
dependencies**. Siblings depend on contracts, not on providers or tools.

## 7. Fail closed on authority

Permissions, sandbox boundaries, and validators block by default. A denied capability surfaces
as `PermissionDenied`, not as silent degradation.

## 8. Verification is first-class

Tasks run plan → sprint → evaluate. Failures name the phase (`RunTaskError.phase`). Evaluator
records are structured JSON on disk, not free-text-only verdicts.

## 9. Scope discipline

If a feature only makes sense for a company/org chart, it does not belong in this repo. Ship
the smallest extension point here; build product semantics upstream.

## 10. Tests are the spec

Behaviour ships with tests. Public API or contract changes update `CHANGELOG.md` and the public
API pin test in the same PR.
