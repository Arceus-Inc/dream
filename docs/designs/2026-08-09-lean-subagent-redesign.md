# Lean Subagent Redesign (2026-08-09)

Hermes + Claude typed-catalog hybrid. Mid-beat spawn stays separate from org
delegation.

## Live path

```
spawn_subagent → run_subagent_delegate → run_subagent_session → run_role
```

## Builtins (always when spawn enabled)

| Type | Posture |
|------|---------|
| `explore` | Read-only map / evidence |
| `plan` | Read-only implementation plan |
| `verify` | Strict PASS/FAIL/PARTIAL JSON |
| `generalPurpose` | Parent ∩ minus nest tools |

Role specialists **add** names; they do not remove builtins. Unknown types refuse.

## Wired declaration fields

- `model` → `SessionOptions.model`
- `permission_overlay` → tighten-only child gate wrapper
- `spawned_by` → fail-closed at resolve
- `isolation` → `shared` | `worktree` (ephemeral git worktree under scratch)

## Host blocklist (Hermes)

Children never receive clarify / memory write / cron / task_create / worktree
enter-exit. Leaves also lose `spawn_subagent`.

## Async

`background=true` returns a handle. Poll/stop via `delegation_get` /
`delegation_stop` (not the shell `task_*` tools). Sync remains the beat default.

## Depth

`MAX_INLINE_NESTING = 2`. Flat by default; depth-2 only when a specialist
declares `spawnable`.

## Chorus lean roster

Keep: `web_research`, DoD graders (`test_author`, `api_verifier`,
`code_reviewer`), critics (`critic`, `brand_critic`, `design_critic`).

Kill from manifests: craft middlemen (researcher wrappers, strategist, creative,
explorer, ux_researcher, analyst personas, ceo advisor/researcher, ui_tester).

## Vendor steals

- OpenHarness: Explore/Plan/verification denylists, worktree isolation, task poll
- Hermes: host blocklist, summary budget + spill, sync default
- OpenCode: explore allowlist posture, filterCompacted mindset
- qm: fail-closed named types only
