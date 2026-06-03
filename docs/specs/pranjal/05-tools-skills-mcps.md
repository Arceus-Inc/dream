# 05 — Tools, Skills, MCPs

**One-liner:** Bash is the default general-purpose tool. Skills are progressively-disclosed playbooks. MCPs are allowlisted external surfaces. Per-repo additions are first-class; harness defaults are a starting point, not a ceiling.

**Sources:** [LC], [OAI], [AHE] · taxonomy §3, §4, §5

---

## Why this matters

The "tool" concept is overloaded in modern agent stacks: anything from a one-line shell helper to a sprawling MCP server with its own auth flow gets called a "tool" in some documents. That looseness causes three problems:

1. The model can't tell which tool to reach for.
2. The operator can't see the full attack surface.
3. The harness can't make uniform decisions about logging, sandboxing, or caching.

This spec separates the three layers — **tools** (the small built-in set the agent always has), **skills** (markdown playbooks loaded on demand), and **MCPs** (external servers behind an allowlist) — and pins down how each is declared, surfaced, and audited.

The opinionated default is bash-first. A well-instrumented shell is a more reliable substrate than a dozen bespoke tools, especially when paired with strong context-engineering rules (#04) that keep its output from drowning the agent.

## Scope

**In:** the default tool set, deterministic tool ordering, skill discovery and disclosure, MCP allowlist, per-repo extension points, integration of validator/lint feedback as agent-visible signal.

**Out:** sandbox tiers and capability ramping (→ #08); context offload of large tool output (→ #04); subagent-specific tool restrictions (→ #06); cron-driven tool execution (→ #09).

## Key decisions (assumed defaults)

1. **Default tools ship with every session:** `bash`, `read_file`, `write_file`, `apply_patch`, `git`, `read_offloaded` (from #04). Nothing else is enabled by default.
2. **Tool order in the prompt is deterministic and stable** across sessions: defaults first, then per-repo additions in alphabetic order, then skills' tool entries (if any). Stability matters for prompt caching (#04).
3. **Tools have a strict schema:** `name`, `description`, `parameters` (JSON Schema), `returns` (free-text), `risk` (one of `safe`, `mutating`, `external`), `tier_required` (sandbox tier from #08).
4. **Skills live at `docs/skills/{slug}/SKILL.md`** with YAML frontmatter: `name`, `description`, `when_to_use`, optional `tools_required` (list of tool names), optional `risk`.
5. **Skill loader is two-step:**
   - At session start: parse frontmatter, validate fields, surface name/description/when_to_use only (#04).
   - On `use_skill(name)`: load body, validate referenced tools are available, inject body into context.
6. **MCPs declared in `.harness/mcp-allowlist.toml`** with: `name`, `endpoint`, `transport` (`stdio` or `http`), `tier_required`, `pinned_version_hash` (optional but recommended).
7. **Unlisted MCPs are refused at session start.** If an MCP server tries to register and isn't on the allowlist, the validator (#01) emits a blocking finding.
8. **Direct runtime integration over MCP** for high-bandwidth surfaces (browser, debugger). MCP is for tool-shaped things; direct integration is for protocol-shaped things (CDP, DAP).
9. **Per-repo tools, skills, and MCPs** can be added without forking the harness:
   - Tools: `.harness/tools/{name}.toml` declares a tool that maps to a binary or script in the repo.
   - Skills: anything under `docs/skills/` is discovered.
   - MCPs: the allowlist file itself is per-repo.
10. **Lint/validator/test failures feed back into the agent's next turn** as a structured "remediation prompt" — not just stderr from a tool call. This makes the harness self-correcting without the agent having to remember to read logs.

## Artefact shapes

### Tool declaration (`.harness/tools/{name}.toml`)

Fields:
- `name`
- `description`
- `command` — shell-style invocation template, with `{arg}` placeholders.
- `parameters` — inline JSON Schema or `$ref` to a schema file.
- `risk` — `safe` | `mutating` | `external`.
- `tier_required` — sandbox tier (#08).
- `timeout_seconds` — per-call wall-clock cap.

### Skill (`docs/skills/{slug}/SKILL.md`)

Frontmatter (YAML):
```yaml
name: <slug>
description: <one sentence>
when_to_use: <one paragraph>
tools_required: [bash, git]   # optional
risk: safe                    # optional, defaults to safe
```

Body: free Markdown — the playbook.

### MCP allowlist entry

```toml
[[mcp]]
name = "playwright"
endpoint = "stdio:///path/to/playwright-mcp"
transport = "stdio"
tier_required = "repo-write+net-allowlist"
pinned_version_hash = "sha256:..."
```

### Remediation prompt (injected on lint/test failure)

Fields:
- `source` — tool name (e.g. `pytest`, `ruff`).
- `summary` — one sentence.
- `details` — head of the actual output (subject to #04 offload).
- `pointer` — file path + line, where parseable.
- `suggested_next_step` — set by the lint/validator integration, not the agent.

## Behaviours

### Tool registration at session start

1. Runner loads default tools.
2. Runner discovers per-repo tools from `.harness/tools/`.
3. Runner discovers skills from `docs/skills/` and adds any `tools_required` to the visible set if not already there.
4. Runner validates each tool's schema and resolves its `tier_required` against the session's sandbox tier (#08); under-tier tools are surfaced but marked `unavailable`.
5. Runner emits the deterministic tool list into the prompt's stable preamble (#04).

### Skill use

1. Agent emits `use_skill(name)`.
2. Runner verifies the skill exists and its `tools_required` are all available; if not, returns an error.
3. Runner loads the body, injects it into context, emits `context.skill.loaded` (#04).
4. Skill body remains in context until session end or eviction by compaction.

### MCP registration

1. At session start, runner reads the allowlist.
2. Runner starts each allowlisted MCP server, verifying `pinned_version_hash` where present.
3. Servers expose their tools; runner registers them with `tier_required` set per the allowlist entry.
4. An MCP server attempting to expose a tool not declared in its allowlist entry is denied (logged at warning severity).

### Lint / validator feedback loop

1. After a tool call that runs a linter, test runner, or validator, the runner inspects the result.
2. If the result is a failure that has a parseable structure, the runner builds a remediation prompt.
3. The remediation prompt is queued; on the agent's next turn, it appears in the `read` step's input under a "follow-ups" header.
4. The agent decides whether to address it.
5. Remediation prompts are recorded in the jsonl as `tool.remediation` events.

## Acceptance criteria

### Default tool set (MUST)

1. **MUST** include `bash`, `read_file`, `write_file`, `apply_patch`, `git`, `read_offloaded` in every session.
2. **MUST** present tools in a deterministic order across sessions.
3. **MUST** include `risk` and `tier_required` for every declared tool.

### Skill loader (MUST)

4. **MUST** load only frontmatter at session start.
5. **MUST** load skill body only on explicit `use_skill` call.
6. **MUST** refuse to load a skill whose `tools_required` are not all available.
7. **MUST** validate skill frontmatter and refuse to start the session on malformed skills.

### MCP allowlist (MUST)

8. **MUST** refuse any MCP not on the allowlist.
9. **MUST** verify `pinned_version_hash` when present and refuse on mismatch.
10. **MUST** deny tools an MCP server tries to expose that aren't declared in its allowlist entry.

### Per-repo extension (MUST/SHOULD)

11. **MUST** discover per-repo tools from `.harness/tools/`.
12. **MUST** discover skills from `docs/skills/`.
13. **SHOULD** allow per-repo additions without modifying harness code.

### Feedback loop (MUST)

14. **MUST** surface lint/validator/test failures as remediation prompts in the next turn.
15. **MUST** record remediation prompts as `tool.remediation` events.
16. **SHOULD** include a parseable pointer (file + line) where the underlying tool provides one.

### Context discipline (MUST)

17. **MUST** route oversized tool outputs through the #04 offload contract.
18. **MUST** include the tool registry in the prompt's stable preamble (#04).

## Acceptance scenarios

```gherkin
Scenario: Default tools are always present
  Given a clean repo with no .harness/tools/ entries
  When the session starts
  Then the agent's tool list contains bash, read_file, write_file, apply_patch, git, read_offloaded
  And no other tools.

Scenario: Tool order is stable across sessions
  Given the same repo and skill set
  When session A starts on Monday and session B starts on Tuesday
  Then the tool list shown to the agent is byte-identical between the two sessions.

Scenario: Skill loads frontmatter only at session start
  Given docs/skills/refactor/SKILL.md with a 500-line body
  When the session starts
  Then the agent's context shows refactor's name, description, when_to_use
  And no body content.

Scenario: Skill body loaded on use_skill call
  Given the refactor skill is registered
  When the agent calls use_skill("refactor")
  Then the body is loaded into context
  And a context.skill.loaded event is emitted.

Scenario: Skill with missing required tool refused
  Given a skill requires tool "browser_drive" which is not in the registry
  When the agent calls use_skill on it
  Then use_skill returns an error
  And no body is loaded
  And an info event records the refusal.

Scenario: Unlisted MCP refused at session start
  Given the allowlist contains only "playwright"
  And the harness is configured to start an MCP server "experimental"
  When the validator runs at session start
  Then a blocking finding is emitted for "MCP not on allowlist: experimental"
  And the session does not start.

Scenario: Pinned MCP version mismatch refused
  Given the allowlist pins playwright at sha256:abc
  And the running playwright binary hashes to sha256:def
  When the runner tries to start playwright
  Then startup fails with a blocking finding for "MCP version mismatch"
  And the session does not start.

Scenario: Per-repo tool discovered without code change
  Given .harness/tools/run_load_test.toml exists in the repo
  When the session starts
  Then the agent's tool list contains run_load_test
  And no harness code was modified.

Scenario: Lint failure becomes a remediation prompt next turn
  Given the agent runs ruff and gets two violations in src/foo.py:12 and src/bar.py:7
  When the next turn begins
  Then the agent's input contains a "follow-ups" section
  And it lists both violations with file+line pointers
  And tool.remediation events are present in the jsonl.

Scenario: Tier-gated tool surfaced as unavailable
  Given a tool requires tier "unrestricted"
  And the session is running at tier "repo-write"
  When tools are registered at session start
  Then the tool is visible in the registry
  And marked unavailable
  And calling it returns an "insufficient tier" error.
```

## Tests

- `test_default_toolset_present` — minimum tools always available.
- `test_tool_order_deterministic_across_sessions` — caching friendliness.
- `test_tool_declaration_requires_risk_and_tier` — schema discipline.
- `test_malformed_skill_frontmatter_blocks_session` — fail fast.
- `test_skill_frontmatter_only_at_session_start` — disclosure.
- `test_use_skill_loads_body` — body load on demand.
- `test_use_skill_refused_when_required_tool_missing` — dependency check.
- `test_use_skill_emits_loaded_event` — observability.
- `test_skill_body_persists_until_compaction` — load semantics.
- `test_unlisted_mcp_refused_at_session_start` — allowlist enforced.
- `test_listed_mcp_starts` — happy path.
- `test_mcp_version_hash_mismatch_refused` — supply-chain guard.
- `test_mcp_undeclared_tool_denied` — surface limited.
- `test_per_repo_tool_discovered` — extensibility.
- `test_per_repo_tool_invocable` — usable end-to-end.
- `test_per_repo_skill_discovered` — extensibility.
- `test_lint_failure_becomes_remediation_prompt` — feedback loop.
- `test_remediation_includes_file_line_pointer` — actionable.
- `test_remediation_logged_as_event` — observability.
- `test_oversized_tool_output_offloaded` — #04 integration.
- `test_tier_gated_tool_surfaced_as_unavailable` — sandbox integration.
- `test_under_tier_call_returns_clear_error` — actionable failure.
- `test_tool_registry_in_stable_preamble` — cache placement.

## Edge cases

- **A tool declaration with no `parameters`.** Allowed; treated as zero-arg.
- **Conflicting tool names** between defaults and per-repo. Per-repo wins; the harness logs a warning at session start (per-repo authors get the rope they ask for).
- **Skill body is empty.** Loaded as empty; agent has nothing to add to context.
- **Skill body references a tool not in `tools_required`.** Not enforced — `tools_required` is a hint to the loader, not a static check on the body. Future work.
- **MCP server crashes mid-session.** Tools it provided become unavailable; calls return an error; runner emits a warning event and continues.
- **Two MCP servers expose the same tool name.** Refused at registration — second one fails with a clear error.

## Open questions

- Whether `tools_required` should be a hard refusal or a warning when a required tool is unavailable (current default: hard refusal).
- Whether per-repo tools should be allowed to override default tools by name (currently allowed with a warning).
- Whether remediation prompts should be coalesced when multiple linters report the same issue.

## Out of scope

- Skill content authoring guidance (deferred — this spec governs the loader, not the playbook style).
- Sandbox tier definitions and ramping policy (→ #08).
- MCP server implementations themselves (external; we consume them).
- Subagent-specific tool restrictions (→ #06).
- Cron-driven tool usage (→ #09).
