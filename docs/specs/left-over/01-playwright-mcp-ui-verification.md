# 01 — Playwright-MCP UI verifier (left over from #12c)

**Deferred from:** `#12` slice 12c (verify-like-a-user runner).
**Status:** not built — the seam exists, the real driver does not.

## What was deferred

Spec 12's decision #1: *"UI verification surface = Playwright MCP driving the
running app — exercise the user-facing path for any change that could affect
UX."* 12c shipped the verification runner and the **`UiVerifier` seam** but only
the **`SkipUiVerifier`** default (records each user-path as `skipped`). The real
verifier — drive a browser through the **Playwright MCP** server, exercise each
declared user-path, capture screenshots/console/network — is this left-over.

## Why it was deferred

- It needs a **live Playwright MCP server on the allowlist** (`.harness/mcp-allowlist.toml`),
  which is *operator configuration*, not a dream spec.
- Driving it means real MCP `call_tool` round-trips against an external process —
  not deterministically unit-testable, and overlapping with operator setup.
- The non-UI shell runner is the independent, fully-tested core that already
  unblocks `#09` (autopilot's verify step) and the `#12e` tech-debt half. The UI
  path is the spec's stated best-effort surface ("Playwright unavailable → fall
  back to non-UI tests; UI assertions recorded as `skipped`, not silently
  passed") — which `SkipUiVerifier` already honours.

## The seam it plugs into

Already shipped in `src/dream/verification/`:

```python
# _ui.py
class UiVerifier(Protocol):
    async def verify(self, user_path: str) -> RepoVerificationStep: ...

class SkipUiVerifier:   # default
    async def verify(self, user_path): -> RepoVerificationStep(status="skipped", ...)
```

`run_verification(steps, *, cwd, ui_paths=(), ui_verifier=SkipUiVerifier())`
already calls `ui_verifier.verify(path)` for each declared user-path. Building
this left-over is purely: implement a `PlaywrightMcpUiVerifier(UiVerifier)` and
pass it in where a Playwright server is configured.

## Scope (when built)

- A `PlaywrightMcpUiVerifier` that takes the session's `McpClientManager` (`#06`)
  and, per user-path, drives the Playwright MCP tools (`navigate`, assertions),
  returning a `RepoVerificationStep` (`success`/`failed`).
- Persist screenshots / console logs / network traces under
  `.dream/sidecars/{task-id}/metrics/playwright/` (Spec 12 decision #9).
- The evaluation record (12d) references at least one Playwright artefact for a
  UI change (Spec 12 acceptance: "UI change triggers browser verification").
- Graceful degradation: if the Playwright server is absent/failed
  (`McpServerNotConnectedError`), return `skipped` with a clear detail — never a
  silent pass, never a crash.

## Acceptance criteria

1. **MUST** drive Playwright MCP for each declared UI user-path when a Playwright
   server is connected.
2. **MUST** write Playwright artefacts under the task's `metrics/playwright/`.
3. **MUST** degrade to `skipped` (not `failed`, not `success`) when the server is
   unavailable.
4. **MUST** return a `RepoVerificationStep` per user-path so results fold into the
   same `VerificationReport` as the shell steps.

## Tests (when built)

- `test_playwright_verifier_drives_declared_user_paths` (against the MCP
  in-memory transport with a fake Playwright server).
- `test_playwright_artefacts_written_to_sidecars`.
- `test_playwright_unavailable_degrades_to_skipped`.

## Dependencies

- `#06` MCP client (shipped) + a Playwright MCP server on the allowlist (operator).
- `#12c` verification runner + `UiVerifier` seam (shipped).
