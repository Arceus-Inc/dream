# 04 — Lurkr `unverified_mcp` + `prompt_interpolation` scan (left over from #13E)

**Deferred from:** `#13` slice 13E (Lurkr threat scan). The three robust
categories ship; these two are deferred.
**Status:** not built.

## What was deferred

Spec 13 decision #6 / AC #11 names **five** session-start blocking categories.
13E shipped the three with high signal and low false-positive rates —
`secret` (worktree-wide), `world_writable` (under `docs/`), and `eval_in_tool`
(AST scan of `.harness/tools/`). The remaining two are this left-over:

- **`unverified_mcp`** — an MCP server admitted on the per-repo allowlist with no
  `pinned_version_hash` while its `tier_required >= repo-write+net-allowlist`.
- **`prompt_interpolation`** — untrusted input string-formatted into a prompt
  (e.g. an f-string / `.format()` of external data into a system/user prompt).

## Why they were deferred

- **`unverified_mcp`** is coupled to MCP-allowlist structure that does not yet
  carry a `pinned_version_hash` field, and to the tier model only just landed in
  `#13B`. Implementing it now would mean inventing the pinning field ahead of the
  MCP layer that should own it.
- **`prompt_interpolation`** is a static heuristic with a high false-positive
  rate: "untrusted input" and "a prompt" are both hard to identify reliably from
  source, and every Lurkr finding is *blocking* — a bad heuristic would abort
  legitimate sessions. It needs a carefully tuned design (and likely a
  suppression story beyond the `.harness/lurkr-ignore.toml` globs) before it can
  be a blocking gate.

## Scope (when built)

- **`unverified_mcp`**: once the MCP allowlist carries `pinned_version_hash`,
  read it; for any server at `tier_required >= repo-write+net-allowlist` lacking
  a hash, emit a blocking `Finding(code="unverified_mcp")`.
- **`prompt_interpolation`**: AST-detect interpolation of non-literal/external
  values into strings used as prompts; start as a **warning** (non-blocking) and
  promote to blocking only after the false-positive rate is measured.

## Acceptance criteria

1. **MUST** block session start on an unverified high-tier MCP (no
   `pinned_version_hash`), reusing the 13E `threat_scan`/`Finding` path.
2. **SHOULD** flag prompt-interpolation patterns (warning first; blocking only
   once tuned), honouring `.harness/lurkr-ignore.toml`.
3. **MUST** redact any secret-shaped value, as the other categories do.

## Dependencies

- MCP allowlist gaining a `pinned_version_hash` field (`#06` MCP layer). **Blocking
  for `unverified_mcp`.**
- `#13E` `threat_scan` module + `Finding` path + `.harness/lurkr-ignore.toml` —
  shipped; the consumer is ready.
