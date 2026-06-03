# 12 — Hooks, Plugins & Protocols

**One-liner:** A small hook bus fires on every lifecycle event. Plugins are repo-local packages that subscribe to hooks and register slash commands. MCPs are the network-protocol surface; plugins are the in-process extension surface.

**Sources:** [LC], [AHE] · taxonomy §22

---

## Why this matters

Almost every harness in the wild grows a layer of operator-specific customisations: "before every commit, run my linter," "after every turn, push metrics to my dashboard," "add a `/reset` slash command that purges scratch." Without an extension surface, those customisations end up patched into the harness core, where they:

- Make harness upgrades painful (every release breaks somebody's fork).
- Spread cross-cutting concerns through unrelated code.
- Can't be turned off without source edits.

The fix is a small, opinionated extension model with three pieces:

1. A **hook bus** — every lifecycle event has a stable name; plugins subscribe.
2. **Plugins** — repo-local packages with a manifest, lifecycle, and capability declaration.
3. **Slash commands** — a registry plugins (and the harness core) can write into, with deterministic dispatch.

External integrations that need their own process get MCPs (→ #05). In-process integrations that need to observe or extend the harness itself get plugins.

## Scope

**In:** the hook bus contract, the plugin manifest and loader, the slash-command registry and dispatch, plugin sandbox interaction, plugin lifecycle (load/start/stop/reload), the per-event payload schemas.

**Out:** the MCP allowlist (→ #05); session/turn state definitions (→ #03); core-belief enforcement (→ #08); cron-job spawning (→ #09); evaluator-side observability schema (→ #07).

## Assumed defaults

- **Hooks are fire-and-forget.** They observe; they don't veto. A hook handler that raises is logged and ignored; it cannot prevent the lifecycle event from completing.
- **Hook firing is synchronous within the runner but bounded.** Each handler gets 1 second of wall-clock. If it doesn't return, the runner moves on and logs a warning. Handlers that need to do real work hand off to a queue (their problem, not ours).
- **Hook names are stable strings, dot-separated.** Pattern: `{subject}.{verb}.{tense}`, e.g. `session.start.before`, `session.start.after`, `turn.complete.after`, `tool.call.before`, `validator.finding.emitted`.
- **Hook catalogue is documented in `docs/_schemas/hook-catalogue.md`.** New hook names require an additive change to that file.
- **Plugins live under `plugins/{plugin-name}/`** in the repo. Each has a `manifest.toml` declaring name, version, entry point, hook subscriptions, slash commands, and required capabilities.
- **Plugin loading is opt-in.** `.harness/plugins-enabled.toml` lists which plugins are active. A plugin present on disk but not enabled is ignored.
- **Plugins run in-process** for v1 (we trust the repo as a security boundary). Per-plugin process isolation is a deferred enhancement.
- **Plugins declare capabilities** (e.g. `repo-write`, `network`, `subprocess`). The runner refuses to load a plugin whose declared capabilities exceed the session's sandbox tier (#08).
- **Slash commands are top-level only.** No `/foo bar` namespacing for v1; `/foo` and `/bar` are flat. Conflicts are refused at load time (first plugin to register wins; the second logs an error and fails to load its command).
- **Built-in slash commands** include at minimum: `/help`, `/status`, `/reset` (clear working memory), `/replan` (mark current exec-plan stale + request planner), `/sandbox-tier` (show current tier).
- **Plugin reload** is supported via a `/reload-plugins` built-in. Reload tears down and re-initialises subscribed plugins.
- **Plugin failure does not abort the session.** A plugin that crashes during init is logged; its hook subscriptions and slash commands are unavailable; the session continues.

## Artefacts

### Plugin manifest (`plugins/{name}/manifest.toml`)

```toml
name = "metrics-pusher"
version = "0.2.0"
entry = "main.py"           # relative to plugin dir
description = "Push session metrics to local Prometheus."

[subscribes]
hooks = [
  "turn.complete.after",
  "session.end.after",
]

[slash_commands]
commands = ["push-now"]

[capabilities]
required = ["network"]
```

### Plugin entry point

A file the loader can `import` / `require`. It must export:
- `setup(runtime)` — called once after load; `runtime` exposes registration APIs.
- `teardown()` — called on unload/reload.

Inside `setup`, the plugin registers handlers and slash commands.

### Slash command record (in-memory registry)

- `name` — `push-now`.
- `owner` — plugin name or `"core"`.
- `handler` — callable.
- `help` — one-line description.

### Hook event payload

Every event payload is a dict with at minimum:
- `event_name` — e.g. `turn.complete.after`.
- `ts` — ISO 8601.
- `session_id`, `task_id` — where applicable.
- Event-specific fields (declared in the catalogue).

### `.harness/plugins-enabled.toml`

```toml
[[plugin]]
name = "metrics-pusher"
version = "0.2.0"           # optional pin; mismatch warns

[[plugin]]
name = "lint-on-commit"
```

## Behaviour

### Plugin loading

1. At runner startup (before session start):
   - Read `plugins-enabled.toml`.
   - For each entry, locate the plugin directory under `plugins/`.
   - Parse the manifest; validate against schema.
   - Check declared `capabilities` against the active sandbox tier; refuse on mismatch with a clear error.
   - Import the entry point; call `setup(runtime)` inside a try/except.
   - On exception: log, mark plugin as failed-to-load, continue.
2. After all plugins loaded, log a summary event `plugins.loaded` with loaded/failed counts.

### Hook firing

1. Runner reaches a lifecycle event (e.g. `turn.complete.after`).
2. Runner looks up subscribers in O(1).
3. For each subscriber, runner calls the handler with the event payload inside a 1-second deadline.
4. Exceptions are caught and logged as `hook.handler.error` events; the runner continues to the next subscriber.
5. After all handlers, runner proceeds with the lifecycle event.

### Slash command dispatch

1. The agent (or operator, via a CLI) emits a slash command in the input stream.
2. Runner extracts the command name; looks up in the registry.
3. If found: call handler with the rest of the line as args; handler returns a result string injected into context as a system message.
4. If not found: return a "command not found" message listing the closest matches.

### Plugin reload (`/reload-plugins`)

1. Call `teardown()` on every loaded plugin in reverse load order; collect exceptions.
2. Clear hook subscriptions and slash command registrations for those plugins.
3. Re-run the load procedure.
4. Return a summary (loaded / unchanged / failed) as the command result.

### Capability gating

- A plugin declaring `network` is refused if the session tier is `read-only` or `repo-write` (no net).
- A plugin declaring `subprocess` is refused under `read-only`.
- Refusals are explicit at load time; the plugin appears in the failed list, and the operator can see exactly which capability mismatched.

### Hook catalogue (the minimum set)

| Hook name | When fired | Key payload fields |
|---|---|---|
| `session.start.before` | Right before session-start validator runs. | `repo_path`. |
| `session.start.after` | After validator passes, before first turn. | `session_id`, `warnings`. |
| `session.end.before` | Before session-end ritual begins. | `session_id`. |
| `session.end.after` | After commit, JSONL sealed. | `session_id`, `commit_sha`. |
| `turn.start.before` | Before assembling turn context. | `session_id`, `turn_index`. |
| `turn.complete.after` | After turn output + verification. | `session_id`, `turn_index`, `verdict`. |
| `tool.call.before` | Before invoking a tool. | `tool_name`, `args_digest`. |
| `tool.call.after` | After tool returns. | `tool_name`, `latency_ms`, `success`. |
| `validator.finding.emitted` | Each finding from #01. | `severity`, `code`, `path`. |
| `substrate.failover` | Per #11. | `from`, `to`, `reason`. |
| `cron.run.complete` | Per #09. | `kind`, `outcome`, `run_id`. |
| `working_memory.compressed` | Per #10. | `task_id`, `before_bytes`, `after_bytes`. |
| `wiki.referenced` | Per #10. | `slug`. |
| `plugins.loaded` | After plugin loading. | `loaded`, `failed`. |

Adding a hook to a future spec is an additive change to this catalogue file.

## Acceptance criteria

1. **MUST** maintain a single hook catalogue in `docs/_schemas/hook-catalogue.md` listing every fired hook and its payload schema.
2. **MUST** fire every catalogued hook at the documented point in every applicable lifecycle.
3. **MUST** treat hook handlers as observers — exceptions logged, never veto the lifecycle event.
4. **MUST** enforce a 1-second per-handler deadline; warn on overruns.
5. **MUST** load plugins listed in `.harness/plugins-enabled.toml` at runner startup.
6. **MUST** validate plugin manifests against a JSON schema; refuse to load malformed manifests.
7. **MUST** refuse to load a plugin whose declared capabilities exceed the session sandbox tier (#08), with a clear error.
8. **MUST** continue session start even if some plugins fail to load.
9. **MUST** dispatch slash commands deterministically; refuse duplicate registrations at load time.
10. **MUST** ship built-in slash commands at minimum: `/help`, `/status`, `/reset`, `/replan`, `/sandbox-tier`, `/reload-plugins`.
11. **MUST** support `/reload-plugins` to teardown + reload without restarting the runner.
12. **MUST** emit a `plugins.loaded` event after the initial load and after every reload.
13. **MUST** log `hook.handler.error` events on handler exceptions, including handler identity and traceback.
14. **SHOULD** report version mismatches between `plugins-enabled.toml` pins and on-disk manifests as warnings, not errors.
15. **SHOULD** support optional plugin pinning by version in `plugins-enabled.toml`.
16. **MUST NOT** allow a plugin to expand its declared capabilities at runtime.
17. **MUST NOT** allow slash commands to mutate the active sandbox tier.

## Gherkin

```gherkin
Feature: Hook bus is observer-only

  Scenario: Handler raises does not abort lifecycle
    Given a plugin subscribed to turn.complete.after that always raises
    When a turn completes
    Then a hook.handler.error event is logged with the traceback
    And the lifecycle event completes normally
    And the session continues to the next turn

  Scenario: Slow handler is timed out
    Given a plugin handler that sleeps for 3 seconds
    When the hook fires
    Then the runner moves on after 1 second
    And a warning event identifies the slow handler
    And the handler is not retried

Feature: Plugin capability gating

  Scenario: Plugin requiring network refused under repo-write tier
    Given a plugin declares capabilities = ["network"]
    And the session sandbox tier is repo-write
    When the runner loads plugins
    Then the plugin is not loaded
    And an error event names the plugin and the missing capability
    And the session continues normally

  Scenario: Plugin requiring network allowed under repo-write+net-allowlist
    Given the same plugin
    And the session sandbox tier is repo-write+net-allowlist
    When the runner loads plugins
    Then the plugin loads successfully

Feature: Slash commands

  Scenario: Duplicate command registration refused at load
    Given plugin A registers /metrics
    And plugin B also registers /metrics
    When the runner loads plugins
    Then plugin A's command is active
    And plugin B fails to load with a "duplicate command" error
    And both load outcomes are reported in plugins.loaded

  Scenario: Reload picks up new plugin
    Given a session is running with plugin A loaded
    When a new plugin B is added to plugins-enabled.toml
    And the agent invokes /reload-plugins
    Then plugin A and plugin B are both loaded after the reload
    And a fresh plugins.loaded event is emitted

  Scenario: Slash command cannot change sandbox tier
    Given a plugin's slash command attempts to set the sandbox tier to unrestricted
    When the command runs
    Then the change is refused
    And a security-violation event is logged
```

## Tests

- `test_hook_catalogue_exists_and_validates` — `docs/_schemas/hook-catalogue.md` parseable.
- `test_every_catalogued_hook_fires_at_lifecycle_point` — instrumented runner check.
- `test_handler_exception_does_not_veto_lifecycle` — raising handler doesn't stop the event.
- `test_handler_exception_logged_with_traceback` — `hook.handler.error` event present.
- `test_handler_deadline_enforced_at_1s` — overrunning handler interrupted.
- `test_handler_overrun_warning_logged` — warning event names the slow handler.
- `test_plugin_loaded_from_enabled_list` — listed plugin loads.
- `test_plugin_not_in_enabled_list_ignored` — plugin on disk but not listed → ignored.
- `test_malformed_manifest_refuses_load` — invalid manifest → plugin doesn't load, session continues.
- `test_capability_exceeds_tier_refuses_load` — network plugin under repo-write tier rejected.
- `test_capability_matches_tier_loads` — same plugin under net-allowlist loads.
- `test_failed_plugin_does_not_abort_session` — load failure tolerated.
- `test_duplicate_slash_command_registration_refused` — second registrant fails.
- `test_unknown_slash_command_returns_helpful_message` — suggestion provided.
- `test_built_in_help_lists_all_commands` — `/help` enumerates registry.
- `test_built_in_status_returns_session_summary` — `/status` content.
- `test_built_in_reset_clears_working_memory` — `/reset` empties working-memory file.
- `test_built_in_replan_marks_exec_plan_stale` — `/replan` mutates exec-plan state.
- `test_built_in_sandbox_tier_returns_tier` — `/sandbox-tier` reports correctly.
- `test_reload_plugins_picks_up_new_plugin` — added plugin loaded after reload.
- `test_reload_plugins_removes_deleted_plugin` — removed plugin teardown'd.
- `test_reload_emits_plugins_loaded_event` — summary event after reload.
- `test_plugin_cannot_expand_capabilities_at_runtime` — runtime cap escalation refused.
- `test_slash_command_cannot_change_sandbox_tier` — security gate enforced.
- `test_version_pin_mismatch_warns_not_errors` — soft signal on version drift.
- `test_hook_payload_matches_catalogue_schema` — payload field set conforms.

## Edge cases

- **Plugin's `setup()` blocks forever.** Loader has a 5-second timeout per plugin init; on timeout, plugin marked failed-to-load with reason `init_timeout`.
- **Plugin holds a reference to an internal runtime object after teardown.** Considered a plugin bug; not the runner's job. The runner replaces the runtime registry on reload so stale references become inert.
- **Hook fired during plugin reload.** Handlers registered at the moment of firing are called; mid-reload state is racy by design — handlers must tolerate spurious invocations.
- **A hook's payload schema changes in a future version.** Treated as a breaking change; spec amendment required. Existing plugins on the old schema are not auto-migrated.
- **Two plugins subscribe to the same hook and order matters.** v1: order is load order (which is `plugins-enabled.toml` order). v2 may add explicit priorities; not in v1.
- **Slash command output is huge.** Subject to the same offload contract as tool output (#04).
- **Plugin tries to register the same slash command as a built-in.** Built-ins win; the plugin's command load fails with a "name reserved" error.

## Open questions

- Should plugins be allowed to declare *new* slash commands at runtime (post-`setup`) or only during `setup`? Current default: only during `setup`. Reload required to add commands after.
- Should we support hook *filtering* (subscribe only when a payload field matches) to reduce plugin overhead? Current default: subscribe to the whole hook, filter inside the handler. Revisit if hook volume causes problems.
- Do we want a "plugin marketplace" / discovery mechanism? Out of scope for v1 — plugins are repo-local and reviewed via PR like any other code.
- Should the 1-second handler deadline be configurable per-hook? Currently global; revisit if needed.

## Out of scope

- Out-of-process plugin sandboxing (subprocess or VM). v1 trusts the repo boundary.
- A plugin API for adding *tools* — that's #05's surface, and we don't duplicate it here.
- A plugin API for adding *substrates* — that's #11's adapter system.
- Plugin-to-plugin direct communication. Plugins coordinate via the hook bus and the repo, like everything else.
- Hot module replacement at the file level (edit-and-reload-without-command). `/reload-plugins` is the only reload mechanism in v1.
