# Deferred — Per-repo validator plug-ins (PR 6.1)

**Origin:** pranjal-01 criteria 20–21 (SHOULD). **Status:** deferred. **Effort:** ~1 PR, no deps.

## What it is

An extension point so a **specific repo can add or soften validation rules without
editing dream's core**. The session-start validator (`services/repo_validator.py`,
shipped in PR 6) has fixed rules (AGENTS.md ≤ 300 lines, exec-plans stale at 7
days, required tree, secret scan, …). Those are dream's opinions. Different repos
have legitimately different needs:

- a docs-heavy repo with a 500-line `AGENTS.md` on purpose,
- a repo that parks long-lived exec-plans and doesn't want the staleness warning,
- a team that wants an *extra* rule dream doesn't ship ("every product-spec must
  start with a `# Status:` line").

Today the only way to change those is to fork dream. This is the escape valve.

## How it works

After the built-in checks, `validate_repo` discovers plug-ins under
`.dream/validators/*.py` and gives each two abilities:

1. **Add findings** — its own custom rules (additive, alongside dream's).
2. **Adjust findings** — react to a built-in finding and change its severity.

```text
findings  = run_builtin_checks(paths)          # PR 6 behaviour
findings += run_plugin_checks(paths)           # plug-in custom rules (additive)
findings  = apply_plugin_adjustments(findings) # plug-ins downgrade built-ins
return findings
```

## The safety rail: downgrade-only + non-downgradable set + audit

This is the load-bearing part. A plug-in may move a finding **down** the severity
ladder only, and may **never** touch a protected finding.

```text
blocking  →  warning  →  info  →  (dropped)      # allowed direction
```

- **Downgrade-only:** a plug-in can turn `blocking → warning` (e.g. "the AGENTS.md
  hard cap is just a warning for us") but can never *upgrade* or invent blocks.
- **Non-downgradable set:** certain codes are off-limits entirely — at minimum
  `{secret_detected}`, plus any other security findings. A plug-in that tries to
  silence a leaked credential is refused.
- **Audit trail:** every accepted downgrade is recorded **on the finding**, so it
  is visible in session logs / git:

```python
@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str
    path: str | None = None
    downgraded_by: str | None = None        # which plug-in changed it
    original_severity: Severity | None = None
```

So a reviewer can always see *"`agents_md_oversized` was `blocking`, downgraded to
`warning` by `relax_caps.py`."* Nothing softens silently.

### Why downgrade-only

Validation is a trust boundary. If a plug-in could rewrite findings arbitrarily,
"drop a file in `.dream/validators/`" becomes "disable dream's safety checks" — and
that file is in the repo, so a compromised agent could neuter the gate. Downgrade-
only + protected security findings + audit gives repos real ergonomic control
without ever lowering the security floor, and keeps every softening visible.

## Integration sketch (small, localized)

In `services/repo_validator.py`:
1. Add `downgraded_by` / `original_severity` to `Finding`.
2. `_NON_DOWNGRADABLE = frozenset({"secret_detected"})` (extend as needed).
3. Discover `.dream/validators/*.py`; import each in a `try/except` (a broken
   plug-in becomes an `info` finding, never a crash — and must run **inside the
   sandbox tier**, see governance below).
4. Call optional `validate(paths) -> list[Finding]` and
   `adjust(finding) -> Finding | None` entry points.
5. `_apply_adjustment(old, new)` enforces: `new.severity` is ≤ `old.severity` on
   the ladder, `old.code not in _NON_DOWNGRADABLE`; on violation, keep the
   original and emit an audit finding naming the offending plug-in.

## Acceptance criteria

- **MUST** auto-discover per-repo validators from `.dream/validators/`.
- **MUST** let a plug-in downgrade (not upgrade) a built-in finding's severity.
- **MUST NOT** allow downgrading any finding in the non-downgradable set.
- **MUST** record the downgrading plug-in + original severity on the finding.
- **MUST NOT** let a broken/raising plug-in crash session-start.

## Why deferred

1. **It's a SHOULD** — PR 6's validator is complete and correct without it.
2. **It overlaps spec 13's plugin system** (`plugins/{name}/`, manifests,
   capability-gating, the hook bus). A bespoke `.dream/validators/` mechanism risks
   being a *second*, inconsistent plugin model. Strong argument to build the plugin
   substrate once in spec 13 and make validators **one kind of plugin** then.

**Decision when picking up:** either ship a clean standalone version now (good demo
of the downgrade-only-with-audit pattern, but likely refactored into spec 13
later), or wait and implement it as a spec-13 plugin.
