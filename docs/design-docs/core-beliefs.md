# Core beliefs

These rules govern every change to `dream`. They are the harness constitution — short on
purpose so they survive context compaction (see spec 04).

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
