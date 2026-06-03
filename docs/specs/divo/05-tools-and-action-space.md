# 05 — Tools & Action Space

**One-liner:** The action space is a small, bash-first set of typed tools behind one uniform
contract — every tool declares a schema-first input, a `risk` class, and a sandbox `tier_required`;
every tool returns a normalized result whose shape the engine can reason about without parsing
prose; and every tool-call is executed as an indivisible atom whose `tool_use`/`tool_result` pair
never splits across a context boundary.

**Sources (source of truth):** `docs/specs/new-specs/05-tools-skills-mcps.md` — the *tools* layer
(default bash-first tool set, deterministic ordering, strict tool schema with `risk`/`tier_required`,
per-repo `.harness/tools/{name}.toml` extension, the lint/validator remediation feedback loop, and
its acceptance criteria/Gherkin/tests for the tool surface) is carried forward and enriched here.
The *skills* and *MCP* layers of that same new-spec move to `#06`. · `#00` invariant #1 (the
tool-call atom: `tool_use` + `tool_result` are indivisible) + invariant #3 (orthogonal components,
attributable failures) + invariant #4 (bounded everything, escalate at the cap) · `#03` (the turn
FSM's `act` step is where tools run; `ToolExecutionStarted`/`ToolExecutionCompleted` stream events
drive the FSM) · `#04` (oversized tool output is offloaded by the `_offload_tool_output_if_needed`
contract; the tool registry sits in the stable cached preamble) · `#08` (sandbox tiers and the
`risk`→tier mapping that gates mutating/external tools).
**Reference (grounding only, not authority):** [openharness] `tools/base.py`
(`BaseTool` ABC with `name`/`description`/`input_model`/`execute`/`is_read_only`/`to_api_schema`,
`ToolRegistry` with `register`/`get`/`list_tools`/`to_api_schema`, `ToolResult`
(`output`/`is_error`/`metadata`), `ToolExecutionContext` (`cwd`/`metadata`/`hook_executor`)),
`tools/bash_tool.py` (`BashTool`: pydantic `BashToolInput`, PTY subprocess, `timeout_seconds` cap,
interactive-command preflight, timeout drain/terminate, `returncode` in metadata),
`tools/file_edit_tool.py` (`FileEditTool`: `old_str`/`new_str`/`replace_all`, sandbox path
validation, edit-approval prompt hook), `tools/grep_tool.py`/`glob_tool.py`/`todo_write_tool.py`
(catalogue shape), `engine/query.py` (the dispatch path: `ToolExecutionStarted` →
`registry.get(name)` → `tool.execute(parsed_input, ctx)` → `ToolResultBlock` →
`ToolExecutionCompleted`; single-vs-parallel tool batches; the dangling-`tool_use` guard) — used to
name the tool abstractions and the execution contract concretely.

---

## Why this matters

The action space is the narrowest part of the agent's world and the one with the highest blast
radius. Every irreversible thing the harness does — mutate a file, run a migration, push a branch,
hit an external API — happens through a tool call. Three failures recur when the action space is
designed loosely:

1. **The model can't choose.** When ten tools have overlapping semantics ("edit", "patch",
   "write", "modify"), the model picks inconsistently and the operator can't predict behaviour.
2. **The operator can't see the surface.** If risk isn't declared per tool, there is no way to
   answer "what can this session do that I'd want to review?" without reading every tool's source.
3. **The harness can't decide uniformly.** Logging, sandboxing, caching, and offload all need a
   single predictable result shape. A tool that returns free prose forces the engine to parse
   English to know whether it succeeded — and that parsing is exactly invariant #3's failure mode
   (a component whose failures aren't attributable).

The conceptual spec (new-spec 05) sets the *policy*: bash-first defaults, a strict tool schema,
deterministic ordering, per-repo extension, and a remediation feedback loop. What it leaves
implicit — and what OpenHarness's `tools/base.py` makes concrete — is the *contract mechanism*: a
single `BaseTool` ABC, a `ToolResult` whose shape is uniform across every tool, a
`ToolExecutionContext` that carries `cwd`/sandbox/hook handles, and an `is_read_only` predicate that
lets the engine pre-classify a call's risk *before* it runs. This spec promotes that mechanism to
first-class so the action space is uniform, auditable, and atom-safe by construction.

Two enrichments are worth calling out up front, because they sharpen the conceptual policy:

1. **`risk` is declared *and* derived.** The static `risk` field on a tool declaration
   (`safe`/`mutating`/`external`) is the *worst-case* class. But many tools are conditionally safe:
   `bash cat foo` reads, `bash rm foo` mutates. OpenHarness's `is_read_only(arguments)` predicate
   lets a tool down-classify a *specific invocation* at call time. The engine uses the declared
   `risk` for the prompt-surface/audit view and `is_read_only` for the per-call gate. The two are
   orthogonal and both matter.

2. **The observation is the product, not the side effect.** A tool's value to the agent is bounded
   by how legible its *result* is. Every `ToolResult` carries a one-line `summary` and structured
   `metadata` (e.g. `returncode`, `timed_out`, `interactive_required`) so the engine — and the next
   turn — can act on the outcome without re-parsing `output`. This is the agent-harness-construction
   observation contract (`status`/`summary`/`next_actions`/`artifacts`) realized on top of
   OpenHarness's leaner `ToolResult(output, is_error, metadata)`.

## Scope

**In:** the `BaseTool` contract and `ToolRegistry`; the default bash-first tool catalogue; the
strict tool declaration schema (`name`/`description`/`parameters`/`returns`/`risk`/`tier_required`/
`timeout_seconds`); deterministic tool ordering for cache stability; the normalized `ToolResult`
observation contract (status/summary/next_actions/artifacts over `output`/`is_error`/`metadata`);
the per-call `is_read_only` gate vs. declared `risk`; the error-recovery contract every tool error
path must satisfy; tool granularity rules (micro/medium/macro); per-repo tool extension via
`.harness/tools/{name}.toml`; the lint/validator/test → remediation-prompt feedback loop; the
tool-call atom guarantee at the dispatch boundary.

**Out:** skill discovery/disclosure and MCP allowlisting (→ `#06` — they ride on this same tool
contract but are surfaced differently); sandbox tier *definitions* and capability ramping (→ `#08` —
this spec consumes `tier_required`, it does not define the tiers); context offload of oversized tool
output (→ `#04` — this spec routes into that contract, the threshold/mechanism live there);
subagent-specific tool restriction sets (→ `#10`); cron/background-task-driven tool execution
(→ `#07`); the streaming engine that yields `ToolExecution*` events (→ `#03`).

## Key decisions (assumed defaults)

1. **One tool contract, no exceptions.** Every tool — built-in, per-repo, or MCP-adapted (`#06`) —
   implements the same `BaseTool` contract: a stable `name`, a one-line `description`, a
   schema-first `input_model` (JSON Schema), an `execute(arguments, context) -> ToolResult`, and an
   `is_read_only(arguments) -> bool` per-call predicate. There is no second-class tool interface.

2. **Default tools ship with every session, bash-first:** `bash`, `read_file`, `write_file`,
   `edit_file` (string-replace patch), `git`, and `read_offloaded` (the `#04` offload reader).
   Nothing else is enabled by default. A well-instrumented shell beats a dozen bespoke tools when
   paired with the `#04` context discipline that keeps its output bounded.

3. **Tool order in the prompt is deterministic and stable** across sessions: defaults first (in a
   fixed canonical order), then per-repo additions in alphabetic order, then any tools contributed
   by loaded skills/MCPs (`#06`). The registry renders into the prompt's stable cached preamble
   (`#04`); a reorder would bust the cache, so order is a contract, not an implementation detail.

4. **Strict tool declaration schema.** Every declared tool carries: `name`, `description`,
   `parameters` (JSON Schema), `returns` (free-text shape hint), `risk` (`safe` | `mutating` |
   `external`), `tier_required` (sandbox tier from `#08`), and `timeout_seconds` (per-call
   wall-clock cap — bounded-everything, invariant #4). Missing `risk` or `tier_required` is a
   session-blocking validation error.

5. **`risk` (declared, worst-case) and `is_read_only` (per-call) are orthogonal gates.** The
   declared `risk` drives the audit/prompt-surface view and the *ceiling* tier a tool can ever
   need. `is_read_only(arguments)` lets a single invocation down-classify itself (e.g. `bash` with a
   read-only command) so the engine can apply a cheaper gate to that specific call. The engine
   computes `is_read_only` *before* execution and records it on the call.

6. **`ToolResult` is normalized and legible.** Every execution returns
   `ToolResult(output, is_error, metadata)`. The engine derives a uniform **observation** from it:
   `status` (`success` | `warning` | `error`, from `is_error` + metadata), a one-line `summary`,
   `next_actions` (actionable follow-ups), and `artifacts` (file paths / IDs / offload refs).
   Tools populate `metadata` with structured facts (`returncode`, `timed_out`,
   `interactive_required`, `bytes_written`, `lines_changed`) so the observation is built from data,
   never from parsing `output`.

7. **Every error path satisfies the error-recovery contract.** A tool that returns
   `is_error=True` MUST provide, in `output` and/or `metadata`: a **root-cause hint** (what went
   wrong), a **safe-retry instruction** (what to change before retrying, or "do not retry"), and an
   **explicit stop condition** (when to stop retrying and escalate). This is what makes recovery
   quality — the third constraint on agent output quality — a property of the tool, not a hope.

8. **Granularity follows risk and round-trip cost.** Use **micro-tools** for high-risk,
   hard-to-reverse operations (deploy, migration, permission change) so each is individually
   gateable and auditable. Use **medium tools** for the common read/edit/search loop (`bash`,
   `edit_file`, `grep`, `glob`). Reserve **macro-tools** for cases where round-trip overhead
   dominates (e.g. an "apply N edits atomically" tool). Avoid catch-all tools with overlapping
   semantics — they degrade action-space quality (the model can't choose).

9. **The tool-call atom is enforced at the dispatch boundary.** The engine emits
   `ToolExecutionStarted`, runs `tool.execute`, builds a `ToolResultBlock`, and emits
   `ToolExecutionCompleted` — and the resulting `tool_use`/`tool_result` pair is treated as
   indivisible by `#04`'s boundary logic (`_split_preserving_tool_pairs`). A turn that ends with a
   `tool_use` lacking its matching `tool_result` is repaired on resume (`#03`'s
   `sanitize_conversation_messages`), never left dangling.

10. **Per-repo tools are first-class, not forks.** `.harness/tools/{name}.toml` declares a tool that
    maps to a binary or script in the repo. Discovered at session start, validated against the same
    schema, surfaced in the deterministic order. Per-repo authors get the rope: a per-repo tool may
    shadow a default by name, logged as a warning at session start.

11. **Lint/validator/test failures become remediation prompts, not silent stderr.** After a tool
    call that runs a linter/test/validator, the engine inspects the result; a parseable failure is
    turned into a structured **remediation prompt** queued for the next turn's `read` step (`#03`),
    and recorded as a `tool.remediation` event (`#04` jsonl). This makes the harness self-correcting
    without the agent having to remember to re-read logs.

## Artefact shapes

### `BaseTool` contract (the uniform interface)

Every tool, regardless of origin, exposes:

- `name: str` — stable, explicit, unique within the registry.
- `description: str` — one line; what the tool does and when to reach for it.
- `input_model` — a JSON-Schema-able typed model (pydantic in the reference); `to_api_schema()`
  renders `{name, description, input_schema}` for the provider.
- `execute(arguments, context: ToolExecutionContext) -> ToolResult` — the async body.
- `is_read_only(arguments) -> bool` — per-call risk down-classification (default `False`).

### `ToolExecutionContext`

The shared handle passed to every `execute`:

- `cwd: Path` — the working directory (a worktree root from `#01`).
- `metadata: dict` — per-session/per-call context (e.g. `edit_approval_prompt` callback, sandbox
  flags, tier).
- `hook_executor` — the `#13` hook dispatch handle (pre/post-tool hooks observe here).

### `ToolResult` (the raw return) and the derived observation

Raw return (what a tool produces):

- `output: str` — the human/agent-facing text (subject to `#04` offload if oversized).
- `is_error: bool` — coarse success/failure.
- `metadata: dict` — structured facts: `returncode`, `timed_out`, `interactive_required`,
  `bytes_written`, `lines_changed`, `offload_ref`, etc.

Derived observation (what the engine surfaces to the next turn), built deterministically from the
raw return:

- `status` — `success` | `warning` | `error` (from `is_error` + metadata signals).
- `summary` — one line (e.g. `exit 0, 3 files changed` / `timed out after 600s`).
- `next_actions` — actionable follow-ups (e.g. `retry with a narrower glob`).
- `artifacts` — file paths / IDs / `offload_ref` for the full output.

### Tool declaration (`.harness/tools/{name}.toml`)

Fields (the strict schema; validated at session start):

- `name`
- `description`
- `command` — shell-style invocation template with `{arg}` placeholders.
- `parameters` — inline JSON Schema or `$ref` to a schema file.
- `returns` — free-text shape hint.
- `risk` — `safe` | `mutating` | `external`.
- `tier_required` — sandbox tier (`#08`).
- `timeout_seconds` — per-call wall-clock cap.

### Remediation prompt (queued on lint/test/validator failure)

- `source` — tool name (e.g. `pytest`, `ruff`).
- `summary` — one sentence.
- `details` — head of the actual output (subject to `#04` offload).
- `pointer` — file path + line, where parseable.
- `suggested_next_step` — set by the lint/validator integration, not the agent.

## Behaviours

### Tool registration at session start

1. Runner loads the default tools in canonical order.
2. Runner discovers per-repo tools from `.harness/tools/` and validates each against the strict
   schema; a missing `risk`/`tier_required`, or a malformed `parameters` schema, blocks the session.
3. Runner adds tools contributed by registered skills/MCPs (`#06`) to the visible set.
4. Runner resolves each tool's `tier_required` against the session's sandbox tier (`#08`);
   under-tier tools are surfaced but marked `unavailable`.
5. Runner renders the deterministic tool list into the prompt's stable cached preamble (`#04`) via
   `registry.to_api_schema()`.

### Single tool-call execution (the `act` step)

1. Engine receives a `tool_use` block from the model; resolves it via `registry.get(name)`.
2. Engine parses the arguments against the tool's `input_model`; a schema-invalid call returns a
   structured `is_error` result with a root-cause hint (no execution).
3. Engine computes `is_read_only(parsed_input)` and records it on the call; applies the per-call
   gate (`#08`) using `min(declared_risk_tier, read_only ? safe : declared)`.
4. Engine emits `ToolExecutionStarted{tool_name, tool_input}` (`#03`).
5. Engine runs `tool.execute(parsed_input, ctx)` under the tool's `timeout_seconds`.
6. Engine wraps the `ToolResult` into a `ToolResultBlock`; routes oversized `output` through the
   `#04` offload contract (`_offload_tool_output_if_needed`), replacing it with a preview + ref.
7. Engine derives the observation (`status`/`summary`/`next_actions`/`artifacts`) and emits
   `ToolExecutionCompleted{tool_name, is_error, ...}` (`#03`).
8. The `tool_use`/`tool_result` pair is appended together — the atom is never split.

### Parallel tool batch

1. When the model emits multiple independent `tool_use` blocks in one turn, the engine may execute
   them concurrently, but it MUST collect *all* `tool_result`s and append them as a single `user`
   message — so the batch's atoms all land together, and no `tool_use` is left without its result.
2. If the turn is interrupted (crash, abort) mid-batch, resume repairs any dangling `tool_use` via
   `sanitize_conversation_messages` (`#03`) before the next call.

### Error path (per the error-recovery contract)

1. A tool that fails returns `is_error=True` with `output`/`metadata` carrying the three required
   parts: root-cause hint, safe-retry instruction, explicit stop condition.
2. The engine surfaces these in the observation's `next_actions`.
3. Repeated identical failures are bounded (`#04`/`#03` unproductive-turn detection); at the cap the
   engine escalates rather than looping (invariant #4).

### Lint / validator feedback loop

1. After a tool call that runs a linter/test/validator, the engine inspects the result.
2. A parseable failure is turned into a remediation prompt and queued.
3. On the agent's next turn, it appears in the `read` step's input under a "follow-ups" header.
4. The agent decides whether to address it.
5. Remediation prompts are recorded as `tool.remediation` events in the jsonl (`#04`).

## Acceptance criteria

### Tool contract (MUST)

1. **MUST** require every tool to implement `name`, `description`, `input_model`,
   `execute`, and `is_read_only` (the uniform `BaseTool` contract).
2. **MUST** render every tool's schema via a single `to_api_schema()` shape
   (`{name, description, input_schema}`).
3. **MUST** pass a `ToolExecutionContext` carrying `cwd`, `metadata`, and the `hook_executor` to
   every `execute`.

### Default tool set (MUST)

4. **MUST** include `bash`, `read_file`, `write_file`, `edit_file`, `git`, `read_offloaded` in every
   session.
5. **MUST** present tools in a deterministic, byte-stable order across sessions (cache friendliness,
   `#04`).
6. **MUST** include `risk` and `tier_required` for every declared tool, and block the session on a
   declaration missing either.

### Observation contract (MUST/SHOULD)

7. **MUST** return a normalized `ToolResult(output, is_error, metadata)` from every `execute`.
8. **MUST** derive a `status`/`summary`/`next_actions`/`artifacts` observation from that result
   without parsing `output` prose.
9. **SHOULD** populate `metadata` with structured facts (`returncode`, `timed_out`,
   `bytes_written`, etc.) sufficient to build the observation from data.

### Risk gating (MUST)

10. **MUST** compute `is_read_only(arguments)` before execution and record it on the call.
11. **MUST** gate each call using the per-call risk (declared `risk` down-classified by
    `is_read_only`) against the session's sandbox tier (`#08`).
12. **MUST** surface an under-tier tool as `unavailable` and return an "insufficient tier" error if
    it is called.

### Error recovery (MUST)

13. **MUST** include a root-cause hint, a safe-retry instruction, and an explicit stop condition in
    every `is_error` result.
14. **MUST** return a structured error (not an exception) for a schema-invalid call, without
    executing the tool.
15. **MUST** bound repeated identical failures and escalate at the cap rather than loop
    (invariant #4).

### Atomicity (MUST)

16. **MUST** append each `tool_use`/`tool_result` as an indivisible pair; a turn ending with a
    dangling `tool_use` is repaired on resume (`#03`), never left unmatched.
17. **MUST** collect all `tool_result`s of a parallel batch into a single `user` message.

### Per-repo extension (MUST/SHOULD)

18. **MUST** discover and validate per-repo tools from `.harness/tools/` against the strict schema.
19. **SHOULD** allow per-repo additions without modifying harness code.
20. **SHOULD** allow a per-repo tool to shadow a default by name, logged as a session-start warning.

### Feedback loop & context discipline (MUST)

21. **MUST** surface lint/validator/test failures as remediation prompts in the next turn and record
    them as `tool.remediation` events.
22. **MUST** route oversized tool output through the `#04` offload contract.
23. **MUST** include the tool registry in the prompt's stable cached preamble (`#04`).

## Acceptance scenarios

```gherkin
Scenario: Every tool satisfies the uniform contract
  Given the default tool set is registered
  When the runner renders the tool schemas for the provider
  Then each tool exposes name, description, and input_schema
  And each tool has an execute and an is_read_only method.

Scenario: Default tools are always present
  Given a clean repo with no .harness/tools/ entries
  When the session starts
  Then the tool list contains bash, read_file, write_file, edit_file, git, read_offloaded
  And no other tools.

Scenario: Tool order is byte-stable across sessions
  Given the same repo and tool set
  When session A starts on Monday and session B starts on Tuesday
  Then the rendered tool list is byte-identical between the two sessions.

Scenario: Declaration missing risk blocks the session
  Given .harness/tools/deploy.toml declares a tool with no risk field
  When the session starts
  Then validation emits a blocking finding for "tool declaration missing risk: deploy"
  And the session does not start.

Scenario: Read-only invocation is down-classified
  Given the bash tool is declared risk=mutating
  When the agent calls bash with command "cat README.md"
  Then is_read_only returns true for that invocation
  And the call is gated as a read, not a mutation.

Scenario: Mutating invocation keeps the declared risk
  Given the bash tool is declared risk=mutating
  When the agent calls bash with command "rm -rf build/"
  Then is_read_only returns false for that invocation
  And the call is gated at the mutating tier.

Scenario: Result is normalized into an observation
  Given the agent runs bash "pytest -q" which exits non-zero
  When the tool returns
  Then the result has is_error=true and metadata.returncode != 0
  And the engine derives status=error with a one-line summary
  And next_actions are present.

Scenario: Error result carries the recovery contract
  Given edit_file is called with an old_str that does not occur in the file
  When the tool returns
  Then is_error=true
  And the output names the root cause (old_str not found)
  And it states the safe-retry instruction (re-read the file, widen the match)
  And it states the stop condition (do not retry more than once without re-reading).

Scenario: Schema-invalid call does not execute
  Given the agent calls edit_file with a missing required "path" argument
  When the engine parses the arguments against input_model
  Then the call returns a structured error
  And the tool body never runs.

Scenario: Timeout is bounded and reported
  Given a tool declares timeout_seconds=600
  And the agent runs a command that never exits
  When 600 seconds elapse
  Then the process is terminated
  And the result is is_error=true with metadata.timed_out=true
  And partial output is included.

Scenario: Tool-call atom is never split
  Given a turn ends immediately after a tool_use with no tool_result
  When the session resumes
  Then sanitize_conversation_messages drops or completes the dangling tool_use
  And no provider call is made with an unmatched tool_use.

Scenario: Parallel batch results land together
  Given the model emits three independent tool_use blocks in one turn
  When the engine executes them
  Then all three tool_results are appended as a single user message.

Scenario: Per-repo tool discovered without code change
  Given .harness/tools/run_load_test.toml exists in the repo
  When the session starts
  Then the tool list contains run_load_test
  And no harness code was modified.

Scenario: Per-repo tool shadows a default with a warning
  Given .harness/tools/git.toml redefines the git tool
  When the session starts
  Then the per-repo git tool is used
  And a session-start warning records the shadow.

Scenario: Lint failure becomes a remediation prompt next turn
  Given the agent runs ruff and gets two violations in src/foo.py:12 and src/bar.py:7
  When the next turn begins
  Then the read step's input contains a "follow-ups" section listing both violations
  And tool.remediation events are present in the jsonl.

Scenario: Tier-gated tool surfaced as unavailable
  Given a tool requires tier "unrestricted"
  And the session runs at tier "repo-write"
  When tools are registered at session start
  Then the tool is visible and marked unavailable
  And calling it returns an "insufficient tier" error.

Scenario: Oversized output is offloaded
  Given a bash command emits 2 MB of stdout
  When the result is processed
  Then the output is offloaded per #04
  And the tool_result holds a preview plus an offload_ref artifact.
```

## Tests

- `test_every_tool_implements_contract` — name/description/input_model/execute/is_read_only present.
- `test_tool_to_api_schema_shape` — `{name, description, input_schema}` for each tool.
- `test_default_toolset_present` — minimum tools always available.
- `test_tool_order_deterministic_across_sessions` — byte-stable ordering.
- `test_tool_declaration_requires_risk_and_tier` — schema discipline.
- `test_declaration_missing_risk_blocks_session` — fail fast.
- `test_result_normalized_to_observation` — status/summary/next_actions/artifacts derived from data.
- `test_metadata_carries_structured_facts` — returncode/timed_out present where applicable.
- `test_is_read_only_downclassifies_read_command` — per-call gate.
- `test_is_read_only_false_for_mutating_command` — mutating stays mutating.
- `test_error_result_has_root_cause_retry_and_stop` — recovery contract enforced.
- `test_schema_invalid_call_does_not_execute` — validation before execution.
- `test_timeout_terminates_and_reports` — bounded wall-clock, partial output.
- `test_tool_call_atom_never_split` — dangling tool_use repaired on resume.
- `test_parallel_batch_results_single_user_message` — atom batch integrity.
- `test_per_repo_tool_discovered` — extensibility.
- `test_per_repo_tool_invocable` — usable end-to-end.
- `test_per_repo_tool_shadows_default_with_warning` — override-with-rope.
- `test_lint_failure_becomes_remediation_prompt` — feedback loop.
- `test_remediation_includes_file_line_pointer` — actionable.
- `test_remediation_logged_as_event` — observability.
- `test_tier_gated_tool_surfaced_as_unavailable` — sandbox integration.
- `test_under_tier_call_returns_clear_error` — actionable failure.
- `test_oversized_tool_output_offloaded` — #04 integration.
- `test_tool_registry_in_stable_preamble` — cache placement.

## Edge cases

- **A tool declaration with no `parameters`.** Allowed; treated as zero-arg.
- **Conflicting tool names** between defaults and per-repo. Per-repo wins; logged as a session-start
  warning (per-repo authors get the rope they ask for).
- **`is_read_only` raises on malformed input.** Treated as not-read-only (fail safe to the stricter
  gate); the call still goes through `input_model` validation, which rejects malformed input first.
- **A tool returns `is_error=False` but a non-zero `returncode` in metadata.** The observation
  surfaces `status=warning`; the engine trusts `is_error` for the coarse gate but exposes the
  discrepancy in `next_actions`.
- **Interactive command** (e.g. `bash` invoking an editor or a prompt). Preflight detects it and
  returns `is_error=True` with `metadata.interactive_required=true` and a non-interactive retry
  hint — it never blocks waiting on stdin.
- **A per-repo tool's `command` template references an undefined `{arg}`.** Caught at session-start
  validation; blocks the session with a pointer to the offending declaration.
- **Two tools declared with the same `name` across per-repo files.** Refused at registration; the
  second fails with a clear error (deterministic ordering requires unique names).
- **A mutating tool runs at a tier that allows it but the specific path is sensitive (`#13`).** The
  per-call gate consults the sandbox path validator; a blocked path returns a sandbox error, not a
  silent no-op.

## Open questions

- Whether `is_read_only` should be allowed to *up*-classify a call (declare a nominally-safe tool's
  specific invocation as mutating) or only ever down-classify (current default: down-classify only;
  declared `risk` is the ceiling).
- Whether the derived observation (`next_actions`) should be model-authored or rule-derived for
  failures the engine doesn't recognize (current default: rule-derived from metadata, empty when
  unknown).
- Whether per-repo tools should be allowed to shadow defaults at all, or only add (current default:
  shadow allowed with a warning).
- Whether remediation prompts should be coalesced when multiple linters report the same issue
  (deferred to `#06`/the feedback-loop integration).

## Out of scope

- Skill discovery, progressive disclosure, and MCP allowlisting — same contract, different surface
  (→ `#06`).
- Sandbox tier *definitions* and capability ramping (→ `#08`); this spec only consumes
  `tier_required`.
- The offload threshold and mechanism for oversized tool output (→ `#04`); this spec only routes
  into it.
- Subagent-specific tool restriction sets (→ `#10`).
- Cron/background-task-driven tool execution (→ `#07`).
- The streaming engine that yields `ToolExecutionStarted`/`ToolExecutionCompleted` (→ `#03`); this
  spec defines what runs between those two events.
