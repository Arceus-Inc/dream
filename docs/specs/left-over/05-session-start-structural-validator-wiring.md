# 05 — Session-start structural validator wiring (left over from #13E)

**Deferred from:** `#13` slice 13E. The Lurkr threat scan is wired into the live
REPL; the spec-01 structural validator is not.
**Status:** built but unwired — `validate_repo` and the combined
`session_guard.session_start_findings` exist; nothing in the running REPL calls
the structural half.

## What was deferred

`#01` shipped `services/repo_validator.validate_repo(paths)` — a session-start
**structural** gate — but it was only ever called from tests. 13E added
`services/session_guard.session_start_findings(paths)` = `validate_repo` +
`threat_scan`, and wired **only `threat_scan`** into `run_session_repl`. Wiring
the structural validator (the rest of `session_guard`) into the live session
start is this left-over.

`validate_repo` enforces the harness's repo contract:

- `AGENTS.md` present, within line caps, all markdown links resolvable;
- required `docs/` tree present and not git-ignored
  (`docs/design-docs/core-beliefs.md`, `docs/exec-plans/active`,
  `docs/product-specs`, `docs/references`, `docs/SECURITY.md`);
- `docs/` JSON well-formed + valid against its declared `$schema`;
- stale exec-plans (warning).

## Why it was deferred

It is **opinionated and disruptive** in a way the threat scan is not. The threat
scan only blocks when there is an actual threat, so it is safe-by-default. The
structural validator blocks **any repo lacking the exact `docs/` tree** with
`required_path_missing` — a bare worktree, or any repo not laid out the
dream/AHE way, would refuse to start. For a harness consumed as a **runtime by
external repos** (the 7-employees goal), hard-coding one specific layout as a
launch requirement is a product decision, not a free win.

## Scope (when built)

The fork to resolve first:

1. **Strict** — wire `session_start_findings` as-is; every repo must have
   `AGENTS.md` + the full `docs/` tree or the harness won't run.
2. **Configurable (recommended)** — ship the current tree as the default but let
   an operator declare the required set (e.g. `.harness/required-paths.toml`), so
   a consumer repo isn't forced into dream's exact layout.
3. **Leave unwired** — structural conformance is not enforced at runtime.

Implementation, once the fork is chosen: in `run_session_repl`, run
`session_start_findings(DreamPaths(repo=work_dir, ...))` (replacing the
threat-scan-only call), `has_blocking` → print + return 3 — the same contract as
the skill / MCP / threat gates. Update REPL tests that use bare worktrees to
supply a conforming tree (or run with the structural gate relaxed).

## Acceptance criteria

1. **MUST** run the structural validator at session start and block on its
   blocking findings, reusing the `has_blocking` / exit-3 contract.
2. **SHOULD** make the required-path set operator-configurable rather than
   hard-coding the dream/AHE tree (option 2).
3. **MUST NOT** regress the threat-scan gate already wired in 13E.

## Dependencies

- `#01` `validate_repo` + `#13E` `session_guard.session_start_findings` — shipped;
  the gate is ready, only the call site + the strict/configurable decision remain.
