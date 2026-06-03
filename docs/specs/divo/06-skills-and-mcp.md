# 06 — Skills & MCP

**One-liner:** Two ways to extend the action space without bloating the prompt or the trust
boundary — **skills** are progressively-disclosed Markdown playbooks (frontmatter always visible,
body loaded only on demand) layered across bundled/user/project/plugin sources, and **MCPs** are
external tool/resource servers admitted only through a per-repo allowlist, version-pinned, tier-
gated, and adapted onto the one tool contract from `#05`.

**Sources (source of truth):** `docs/specs/new-specs/05-tools-skills-mcps.md` — the *skills* layer
(skill location/frontmatter contract `name`/`description`/`when_to_use`/`tools_required`/`risk`, the
two-step loader = frontmatter at session start + body on `use_skill`, the `tools_required`
dependency refusal, persistence until compaction) and the *MCP* layer (`.harness/mcp-allowlist.toml`
with `name`/`endpoint`/`transport`/`tier_required`/`pinned_version_hash`, refuse-unlisted-at-start,
verify-pin-or-refuse, deny-undeclared-tools, direct-runtime-over-MCP for high-bandwidth surfaces) are
carried forward and enriched here. The *tools* layer of that same new-spec lives in `#05`. · `#05`
(the `BaseTool`/`ToolResult`/`ToolRegistry` contract that both skill-contributed tools and MCP-
adapted tools must satisfy; deterministic tool ordering) · `#04` (progressive disclosure is a
context-budget mechanism: only frontmatter sits in the stable preamble; loaded skill bodies persist
until compaction evicts them; `context.skill.loaded` event) · `#01` (skills/allowlist are repo
artefacts under version control — the repo is the system of record) · `#08` (every MCP and every
skill-contributed tool carries a `tier_required` gate) · `#13` (plugins/hooks supply additional
skills and MCP servers; the plugin manifest is the trust unit).
**Reference (grounding only, not authority):** [openharness] `skills/types.py`
(`SkillDefinition`: `name`/`description`/`content`/`source`/`command_name`/`aliases`/
`user_invocable`/`disable_model_invocation`/`model`/`argument_hint`), `skills/loader.py`
(`load_skill_registry`, layered precedence bundled → user → project → plugin, multi-dir discovery
`.openharness/skills`/`.agents/skills`/`.claude/skills`, `allow_project_skills` gate,
`_frontmatter.parse_skill_frontmatter`), `skills/registry.py` (`SkillRegistry.register`/`get`),
`tools/skill_tool.py` (`SkillTool`: `is_read_only=True`, case-variant lookup,
`disable_model_invocation` → user-only `/name`), `mcp/types.py` (`McpStdioServerConfig`/
`McpHttpServerConfig`/`McpWebSocketServerConfig`, `McpToolInfo`, `McpResourceInfo`,
`McpConnectionStatus` state machine `connected`/`failed`/`pending`/`disabled`), `mcp/config.py`
(`load_mcp_server_configs`, plugin namespacing `{plugin}:{server}`, settings-win merge),
`mcp/client.py` (`McpClientManager.connect_all`/`reconnect_all`/`call_tool`/`close`,
`McpServerNotConnectedError`), `tools/mcp_tool.py` (`McpToolAdapter`: `mcp__{server}__{tool}` naming,
`_input_model_from_schema` building a pydantic model from the server's JSON Schema),
`tools/mcp_auth_tool.py`/`list_mcp_resources_tool.py`/`read_mcp_resource_tool.py` (auth + resource
surface) — used to name the skill/MCP primitives concretely.

---

## Why this matters

The default tool set (`#05`) is deliberately tiny — bash-first plus a handful of file/git tools.
That keeps the action space legible and the prompt cheap. But real work needs more: a 500-line
"how we do migrations here" playbook, a browser-automation server, a company-specific deploy
helper. The naive answer — dump it all into the system prompt and register every external server —
fails twice over:

1. **Context budget.** Every always-visible token competes with the agent's working memory. A dozen
   long playbooks in the prompt is a dozen things the model half-reads and the cache pays for on
   every turn.
2. **Trust boundary.** Every external server is attack surface. If any process that *claims* to be
   an MCP server can register tools, the operator has lost the ability to answer "what can this
   session reach?" — which is exactly the auditability that invariant #3 (attributable failures) and
   `#13` (governance) depend on.

Skills and MCPs are the two answers, and they share a design spine: **declare cheaply, load
deliberately, gate uniformly.**

- **Skills** solve the context-budget problem with *progressive disclosure*. The conceptual spec
  (new-spec 05) fixes the two-step contract: frontmatter (`name`/`description`/`when_to_use`) is the
  only thing in the prompt at session start; the body loads only when the agent explicitly reaches
  for it. OpenHarness's `SkillDefinition`/`load_skill_registry` make that concrete and add an
  enrichment worth promoting: skills come from **four layered sources** (bundled defaults → user-
  global → project-local → plugin-supplied), with project skills gated behind an
  `allow_project_skills` setting and a `disable_model_invocation` flag that lets a skill be
  *user-only* (a `/command`) rather than model-invocable. Disclosure isn't just "frontmatter vs
  body" — it's also "who is allowed to pull this in."

- **MCPs** solve the trust-boundary problem with an *allowlist*. The conceptual spec fixes the
  security posture: a per-repo `.harness/mcp-allowlist.toml`, unlisted servers refused at session
  start, `pinned_version_hash` verified-or-refused (supply-chain guard), and undeclared tools
  denied. OpenHarness's `McpClientManager`/`McpToolAdapter` show the mechanism: each admitted server
  is connected over a typed transport (stdio/http/ws), its tools are wrapped as ordinary `BaseTool`s
  named `mcp__{server}__{tool}`, and its resources are surfaced through dedicated list/read tools.
  The key reconciliation: OpenHarness *configures* MCP servers from settings+plugins; this spec
  keeps the conceptual allowlist as the *authority* layered on top — config says what *could*
  connect, the allowlist says what *may*.

The opinionated default that survives from new-spec 05: **direct runtime integration beats MCP for
high-bandwidth, protocol-shaped surfaces** (a browser over CDP, a debugger over DAP). MCP is for
tool-shaped things; a chatty bidirectional protocol should be integrated directly, not tunnelled
through tool calls.

## Scope

**In:** the skill artefact (location, frontmatter contract, body); the `SkillRegistry` and its
layered loader (bundled/user/project/plugin precedence); progressive two-step disclosure (frontmatter
at start, body on `use_skill`/`skill`); the `tools_required` dependency check; `user_invocable` /
`disable_model_invocation` semantics; skill persistence-until-compaction; the MCP allowlist artefact
and its fields; MCP admission (refuse-unlisted, verify-pin, deny-undeclared); the three transports
(stdio/http/ws); the `McpToolAdapter` mapping an MCP tool onto the `#05` tool contract; MCP resource
surfacing (list/read); MCP connection-status state machine and mid-session failure handling; MCP auth
flow; plugin-supplied skills/MCPs and their namespacing; direct-runtime-over-MCP guidance.

**Out:** the base tool contract itself (`#05` — skills and MCP tools *ride on* it); sandbox tier
definitions (`#08` — this spec consumes `tier_required`); the plugin manifest format, plugin trust
model, and hook events (`#13` — this spec consumes the skills/MCPs a plugin supplies); skill
*authoring* style guidance (the loader is specified, the playbook prose style is not); the streaming
engine and `use_skill`/tool dispatch mechanics (`#03`); MCP server *implementations* (external — we
consume them).

## Key decisions (assumed defaults)

1. **Skills live at `docs/skills/{slug}/SKILL.md`** (the conceptual home, version-controlled, `#01`),
   and the loader additionally discovers the compatibility layers OpenHarness supports
   (`.openharness/skills`, `.agents/skills`, `.claude/skills`) so existing skill libraries import
   without rewriting. Frontmatter is YAML.

2. **Skill frontmatter contract:** required `name`, `description`, `when_to_use`; optional
   `tools_required` (list of tool names), `risk` (defaults `safe`), `user_invocable` (defaults
   `true`), `disable_model_invocation` (defaults `false`), `aliases`, `argument_hint`, `model`.
   Malformed frontmatter blocks the session (fail fast).

3. **Two-step disclosure (the core budget mechanism).**
   - *At session start:* parse frontmatter, validate fields, surface **only** `name`/`description`/
     `when_to_use` into the stable cached preamble (`#04`). No body.
   - *On `skill(name)` / `use_skill(name)`:* load the body, verify `tools_required` are all
     available, inject the body into context, emit `context.skill.loaded` (`#04`).

4. **Layered source precedence (enrichment):** bundled defaults → user-global → project-local →
   plugin-supplied, registered in that order into one `SkillRegistry`. Project skills are gated
   behind an `allow_project_skills` setting (default on); a later source may shadow an earlier one by
   name, logged at session start.

5. **`disable_model_invocation` makes a skill user-only.** Such a skill is *not* offered to the model
   as something it can pull in; it is invocable only as an operator `/command` (`command_name`). The
   `skill` tool refuses to load it for the model with a clear message. This is a disclosure *and* a
   governance control.

6. **`tools_required` is a hard refusal.** If a skill's body needs a tool that isn't in the registry
   (or is under-tier and `unavailable`, `#05`/`#08`), `use_skill` returns an error and loads no body.
   A skill is only as usable as its dependencies.

7. **The `skill` tool is read-only.** Loading a skill body never mutates anything; `is_read_only`
   returns `true`, so it is gated at the lowest tier (`#05`).

8. **Skill bodies persist until compaction.** Once loaded, a body stays in context until session end
   or until `#04` compaction evicts it; eviction is recorded so a re-`use_skill` is possible.

9. **MCPs are admitted only through `.harness/mcp-allowlist.toml`.** Config (settings + plugin
   manifests, `#13`) declares what servers *exist*; the allowlist declares what *may* connect this
   session. A server present in config but absent from the allowlist is refused at session start
   with a blocking validator finding (`#01`). The allowlist is the authority.

10. **Allowlist entry fields:** `name`, `endpoint`, `transport` (`stdio` | `http` | `ws`),
    `tier_required` (`#08`), `pinned_version_hash` (optional but recommended). Plugin-supplied
    servers are namespaced `{plugin}:{server}` so two plugins can't collide.

11. **Supply-chain guard.** When `pinned_version_hash` is present, the runner verifies the running
    server binary/manifest hash and **refuses on mismatch** with a blocking finding. No pin → a
    warning is logged but the session proceeds (the operator opted out of pinning).

12. **MCP tools are adapted onto the `#05` contract.** Each admitted server's tools become
    `BaseTool`s named `mcp__{server}__{tool}`, with an `input_model` synthesized from the server's
    advertised JSON Schema. They register with the `tier_required` from the allowlist entry and take
    their place in the deterministic tool order (`#05`).

13. **Undeclared tools are denied.** A server that advertises a tool not covered by its allowlist
    entry has that tool denied (logged at warning), so a compromised or drifted server can't silently
    widen its surface.

14. **MCP resources are surfaced through dedicated tools,** not auto-injected: `list_mcp_resources`
    and `read_mcp_resource` let the agent enumerate and pull a server's resources on demand — same
    progressive-disclosure spirit as skills.

15. **Connection failures are typed and non-fatal mid-session.** A server has a status
    (`connected`/`failed`/`pending`/`disabled`); a server that fails to connect at start is `failed`
    and its tools are absent; a server that drops mid-session makes its tools return a
    `McpServerNotConnectedError`-shaped tool error (`#05` error-recovery contract), not a crash.

16. **Direct runtime over MCP for high-bandwidth surfaces.** Browser (CDP), debugger (DAP), and
    similar protocol-shaped integrations are integrated directly, not exposed as MCP tools. MCP is
    reserved for tool-shaped request/response surfaces.

## Artefact shapes

### Skill (`docs/skills/{slug}/SKILL.md`)

Frontmatter (YAML):
```yaml
name: <slug>
description: <one sentence>
when_to_use: <one paragraph>
tools_required: [bash, git]     # optional
risk: safe                      # optional, defaults to safe
user_invocable: true            # optional, defaults to true
disable_model_invocation: false # optional, defaults to false → true = user-only /command
aliases: [refactor, cleanup]    # optional
argument_hint: "<path>"         # optional
model: <model-id>               # optional, pin a model for this skill
```
Body: free Markdown — the playbook.

### `SkillDefinition` (the loaded, in-registry shape)

`name`, `description`, `content` (body), `source` (`bundled`|`user`|`project`|`plugin`), `path`,
`base_dir`, `command_name`, `display_name`, `aliases`, `user_invocable`, `disable_model_invocation`,
`model`, `argument_hint`.

### MCP allowlist entry (`.harness/mcp-allowlist.toml`)

```toml
[[mcp]]
name = "playwright"
endpoint = "stdio:///path/to/playwright-mcp"
transport = "stdio"                       # stdio | http | ws
tier_required = "repo-write+net-allowlist"
pinned_version_hash = "sha256:..."        # optional but recommended
```

### MCP runtime types (from the client)

- `McpToolInfo` — `server_name`, `name`, `description`, `input_schema`.
- `McpResourceInfo` — `server_name`, `name`, `uri`, `description`.
- `McpConnectionStatus` — `name`, `state` (`connected`|`failed`|`pending`|`disabled`), `detail`,
  `transport`, `auth_configured`, `tools[]`, `resources[]`.

### Adapted MCP tool

A `BaseTool` (`#05`) with `name = mcp__{sanitized_server}__{sanitized_tool}`, `description` from the
server, `input_model` synthesized from `input_schema`; `execute` calls
`McpClientManager.call_tool(server, tool, args)` and wraps the response in a `ToolResult`
(`McpServerNotConnectedError` → `is_error=True`).

## Behaviours

### Skill registration at session start

1. Loader registers bundled skills, then user skills, then (if `allow_project_skills`) project
   skills discovered under the configured dirs, then plugin-supplied skills — into one
   `SkillRegistry`, later sources shadowing earlier by name (logged).
2. For each skill, `parse_skill_frontmatter` validates required fields; a malformed frontmatter
   blocks the session.
3. Only `name`/`description`/`when_to_use` (and, for user-invocable ones, the `/command_name`) are
   surfaced into the stable preamble (`#04`). Bodies are not loaded.
4. Each skill's `tools_required` are noted but not yet enforced (enforced on use).

### Skill use (`skill(name)` / `use_skill(name)`)

1. Agent calls the `skill` tool with a name (case-variant lookup tolerated).
2. If the skill is `disable_model_invocation`, the tool refuses with "can only be invoked by the user
   as /name" and loads nothing.
3. Loader verifies `tools_required` are all present and available (`#05`/`#08`); if not, returns an
   error and loads no body.
4. Loader injects the body into context and emits `context.skill.loaded` (`#04`).
5. The body persists until session end or compaction eviction.

### MCP admission at session start

1. Runner merges server configs from settings + enabled plugins (`load_mcp_server_configs`,
   plugin-namespaced).
2. Runner reads `.harness/mcp-allowlist.toml`; any configured server **not** on the allowlist yields
   a blocking validator finding (`#01`) and the session does not start.
3. For each allowlisted server, the runner connects over its transport (`connect_all`); on connect it
   verifies `pinned_version_hash` where present and refuses on mismatch.
4. The server's advertised tools are filtered against its allowlist entry; undeclared tools are
   denied (warning). Surviving tools are wrapped as `McpToolAdapter` `BaseTool`s with the entry's
   `tier_required` and registered in deterministic order (`#05`).
5. Resources are recorded in the server's `McpConnectionStatus`; surfaced only via
   `list_mcp_resources`/`read_mcp_resource`.

### MCP call & mid-session failure

1. The agent calls an `mcp__{server}__{tool}` tool like any other (`#05` dispatch).
2. The adapter calls `McpClientManager.call_tool`; a healthy call returns a `ToolResult`.
3. If the server is not connected (crashed/dropped), the adapter returns `is_error=True` with the
   `#05` recovery contract (root cause = server disconnected; safe-retry = reconnect/await;
   stop = after N failures, escalate). The session continues; the server's status flips to `failed`.

### MCP auth

1. A server requiring auth surfaces `auth_configured=false` in its status.
2. The agent (or operator) uses the `mcp_auth` tool to supply/refresh credentials.
3. On success the server reconnects (`reconnect_all`) and its tools become available.

## Acceptance criteria

### Skill disclosure (MUST)

1. **MUST** surface only `name`/`description`/`when_to_use` (frontmatter) at session start, never the
   body.
2. **MUST** load a skill body only on an explicit `skill`/`use_skill` call.
3. **MUST** validate skill frontmatter and refuse to start the session on malformed frontmatter.
4. **MUST** emit a `context.skill.loaded` event when a body is loaded.
5. **MUST** keep a loaded skill body in context until session end or compaction eviction (`#04`).

### Skill dependencies & invocation (MUST)

6. **MUST** refuse to load a skill whose `tools_required` are not all available, and load no body.
7. **MUST** refuse to load a `disable_model_invocation` skill for the model, surfacing it as
   user-only `/command`.
8. **MUST** treat the `skill`/`use_skill` tool as read-only (lowest tier).

### Skill sourcing (MUST/SHOULD)

9. **MUST** discover skills from `docs/skills/` and the configured compatibility dirs.
10. **SHOULD** layer sources bundled → user → project → plugin, later shadowing earlier with a logged
    warning.
11. **MUST** gate project-skill loading behind the `allow_project_skills` setting.

### MCP admission (MUST)

12. **MUST** refuse any configured MCP server not on `.harness/mcp-allowlist.toml`, with a blocking
    finding, and not start the session.
13. **MUST** verify `pinned_version_hash` when present and refuse on mismatch with a blocking finding.
14. **MUST** deny tools an MCP server advertises that aren't covered by its allowlist entry (logged).
15. **MUST** register surviving MCP tools through the `#05` `BaseTool` contract with the entry's
    `tier_required`, in deterministic order.

### MCP runtime (MUST/SHOULD)

16. **MUST** return a `#05`-shaped tool error (not a crash) when a server is disconnected mid-session.
17. **MUST** surface MCP resources only via `list_mcp_resources`/`read_mcp_resource`, never
    auto-inject them.
18. **SHOULD** track a per-server `McpConnectionStatus` (`connected`/`failed`/`pending`/`disabled`)
    and expose it to the operator.
19. **SHOULD** support stdio, http, and ws transports; an unsupported transport in the current build
    yields a `failed` status with a clear detail, not a crash.

### Integration discipline (SHOULD)

20. **SHOULD** integrate high-bandwidth protocol surfaces (browser/CDP, debugger/DAP) directly rather
    than via MCP.

## Acceptance scenarios

```gherkin
Scenario: Skill shows frontmatter only at session start
  Given docs/skills/refactor/SKILL.md with a 500-line body
  When the session starts
  Then the agent's context shows refactor's name, description, and when_to_use
  And no body content.

Scenario: Skill body loaded on use
  Given the refactor skill is registered
  When the agent calls skill("refactor")
  Then the body is loaded into context
  And a context.skill.loaded event is emitted.

Scenario: Malformed skill frontmatter blocks the session
  Given a skill whose frontmatter is missing the required description
  When the session starts
  Then a blocking finding is emitted
  And the session does not start.

Scenario: Skill with missing required tool refused
  Given a skill requires tool "browser_drive" which is not in the registry
  When the agent calls skill on it
  Then the call returns an error
  And no body is loaded.

Scenario: User-only skill refused for the model
  Given a skill has disable_model_invocation=true and command_name "release"
  When the agent (model) calls skill("release")
  Then the tool returns "can only be invoked by the user as /release"
  And no body is loaded.

Scenario: Project skill gated by setting
  Given allow_project_skills is false
  And docs/skills/local-helper/SKILL.md exists in the repo
  When the session starts
  Then local-helper is not registered.

Scenario: Later source shadows earlier by name
  Given a bundled skill "deploy" and a project skill "deploy"
  When the registry loads
  Then the project "deploy" is used
  And a session-start warning records the shadow.

Scenario: Unlisted MCP refused at session start
  Given config declares servers "playwright" and "experimental"
  And the allowlist contains only "playwright"
  When the session starts
  Then a blocking finding is emitted for "MCP not on allowlist: experimental"
  And the session does not start.

Scenario: Pinned MCP version mismatch refused
  Given the allowlist pins playwright at sha256:abc
  And the running playwright server hashes to sha256:def
  When the runner connects playwright
  Then connection fails with a blocking finding for "MCP version mismatch"
  And the session does not start.

Scenario: Undeclared MCP tool denied
  Given the playwright allowlist entry covers tools [navigate, click]
  And the server advertises an extra tool "exec_shell"
  When tools are registered
  Then navigate and click are registered as mcp__playwright__navigate / __click
  And exec_shell is denied with a warning.

Scenario: MCP tool rides the #05 contract
  Given playwright is admitted with tier_required "repo-write+net-allowlist"
  When its tools register
  Then each is a BaseTool named mcp__playwright__<tool>
  And carries the entry's tier_required
  And appears in the deterministic tool order.

Scenario: MCP server drops mid-session
  Given playwright was connected
  And its process exits mid-session
  When the agent calls mcp__playwright__navigate
  Then the tool returns is_error=true with a server-disconnected root cause and retry hint
  And the session continues
  And playwright's status becomes failed.

Scenario: MCP resources are pulled on demand
  Given a server exposes 40 resources
  When the session starts
  Then none are injected into context
  And the agent can enumerate them via list_mcp_resources
  And read one via read_mcp_resource.
```

## Tests

- `test_skill_frontmatter_only_at_session_start` — disclosure.
- `test_use_skill_loads_body` — body load on demand.
- `test_use_skill_emits_loaded_event` — observability.
- `test_malformed_skill_frontmatter_blocks_session` — fail fast.
- `test_use_skill_refused_when_required_tool_missing` — dependency check.
- `test_disable_model_invocation_refused_for_model` — user-only governance.
- `test_skill_tool_is_read_only` — lowest-tier gating.
- `test_skill_body_persists_until_compaction` — load semantics.
- `test_skill_sources_layered_precedence` — bundled→user→project→plugin.
- `test_project_skills_gated_by_setting` — allow_project_skills.
- `test_later_source_shadows_with_warning` — shadow semantics.
- `test_unlisted_mcp_refused_at_session_start` — allowlist enforced.
- `test_listed_mcp_connects` — happy path.
- `test_mcp_version_hash_mismatch_refused` — supply-chain guard.
- `test_mcp_undeclared_tool_denied` — surface limited.
- `test_mcp_tool_adapted_to_base_tool_contract` — #05 integration.
- `test_mcp_tool_in_deterministic_order` — cache friendliness.
- `test_mcp_server_disconnect_returns_tool_error` — recovery contract.
- `test_mcp_resources_pulled_on_demand` — progressive disclosure.
- `test_mcp_connection_status_tracked` — observability.
- `test_unsupported_transport_marks_failed_not_crash` — robustness.
- `test_mcp_auth_reconnects_server` — auth flow.
- `test_plugin_mcp_namespaced` — {plugin}:{server} collision avoidance.

## Edge cases

- **Skill body is empty.** Loaded as empty; the agent gains nothing, no error.
- **Skill body references a tool not in `tools_required`.** Not statically enforced — `tools_required`
  is a loader hint, not a body lint. Future work.
- **Two skills with the same `name` from the same source.** The later registration wins within a
  source; cross-source shadowing follows the precedence order and is logged.
- **Case-variant skill name** (`Refactor` vs `refactor`). The `skill` tool tolerates common case
  variants on lookup.
- **An MCP server advertises zero tools.** Admitted, registers nothing, status `connected`; its
  resources (if any) are still enumerable.
- **Two MCP servers expose the same tool name.** No collision — names are namespaced
  `mcp__{server}__{tool}`; the server segment disambiguates.
- **An allowlist entry with no `pinned_version_hash`.** Allowed; a warning is logged that the server
  is unpinned (operator opted out of the supply-chain guard).
- **Plugin disabled after supplying an MCP server.** The server config is dropped on reload; its
  tools disappear from the registry on the next session.
- **A server requiring auth with `auth_configured=false`.** Tools are surfaced but calls fail with a
  recovery hint pointing at `mcp_auth` until credentials are supplied.

## Open questions

- Whether `tools_required` should ever be a soft warning instead of a hard refusal (current default:
  hard refusal — a skill is only as usable as its dependencies).
- Whether project skills should be allowed to shadow bundled/user skills at all, or only add
  (current default: shadow allowed with a warning).
- Whether the allowlist should be able to *narrow* a server's tool set per-tool (covered) vs. only
  admit/deny the whole server (current default: per-tool coverage in the entry, undeclared denied).
- Whether unpinned MCP servers should be refused in high-governance modes (`#13`) rather than merely
  warned (deferred to governance policy).
- Whether ws transport is in-scope for the first build or stdio+http only (reference build treats ws
  as best-effort).

## Out of scope

- The base `BaseTool`/`ToolResult` contract (`#05`); skills' contributed tools and MCP-adapted tools
  ride on it.
- Sandbox tier *definitions* and ramping (`#08`); this spec consumes `tier_required`.
- The plugin manifest format, plugin trust model, and hook events (`#13`); this spec consumes the
  skills/MCP servers a plugin supplies.
- Skill *authoring*/playbook-prose style guidance (the loader is governed here, not the writing).
- The streaming engine and `use_skill`/tool dispatch mechanics (`#03`).
- MCP server *implementations* themselves (external; we consume them).
