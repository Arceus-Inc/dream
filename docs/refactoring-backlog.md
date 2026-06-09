# Refactoring Backlog — `dream` harness

> Generated from a full-codebase audit (6 parallel agents, all 237 `src/dream/**.py` files) against the repo's own [Python Refactoring Checklist](./clean-code-python-refactoring-checklist.md) (sections A–J) plus four readability rules: **(1)** trust the call chain (no config-laundering middleman), **(2)** a function must earn its name (no trivial single-call wrappers), **(3)** show the shape (example-shape comments on untyped dicts), **(4)** decompose into named steps (split monoliths).
>
> **How to use:** each row is a yes/no gate — apply the fix or note why you consciously skipped it. Every finding cites a verified `file:line`. Tackle the **Cross-cutting consolidations** first (one fix removes duplication across many files), then the **Monolith decompositions**, then per-package items as you touch those files.
>
> **Scope guardrails (already satisfied — do not "fix"):** `src/` uses **no `logging`** by design (errors surface as structured `Findings` / `ToolResult` metadata / events); immutability via frozen dataclasses + tuples is the norm; **ruff + mypy(strict) already pass**; the `BaseTool` / `ToolDeclaration` / `effects_for` per-tool contract is a deliberate framework interface, not a fat-interface smell.

## Severity summary

| Severity | Count | Meaning |
|---|---|---|
| **High** | ~24 | Real maintainability cost now: god-modules, dispatch ladders, duplication across many files. Worth scheduled work. |
| **Med** | ~50 | Localized readability/shape gaps; do opportunistically when touching the file. |
| **Low** | ~30 | Nits and micro-DRY; safe quick wins. |

Overall the codebase is **high quality** — most packages are clean frozen-dataclass modules with narrow excepts and structured returns. The signal concentrates in: (a) a handful of god-modules/monolith functions, (b) a few **DRY patterns duplicated across many files**, and (c) pervasive `dict[str, Any]` payloads lacking a one-line example-shape comment.

---

## 1. Cross-cutting consolidations (highest ROI — one fix, many files)

These each remove a pattern copy-pasted across N files. Do them first; they shrink the per-package tables below.

### 1.1 — `[High] C14` The `_err(...)` tool-error envelope is defined verbatim in ~14 builtin tools
The identical 9-line `_err(content, *, root_cause, safe_retry, stop_condition)` helper lives in `file_write.py:85`, `git.py:240`, `task_create.py:178`, and ~11 more builtin tool files.
**Fix:** hoist one `tool_error(...)` into `tools/builtin/_errors.py` (or `tools/_base.py`) and import everywhere. Fold the variant `observability_query.py:141 _bad_query` and the `mcp_tool.py:96` / `mcp_resources.py:74` "server unavailable" blocks into it.

### 1.2 — `[High] C14/B11` Path-confinement + escape-error block repeated across all filesystem/exec tools
`resolve_within(...)` wrapped in `PathEscapesRoot → _err("Path outside the working directory…")` is duplicated in `file_read.py:51`, `file_write.py:45`, `file_edit.py:46`, and `bash.py:139`.
**Fix:** extract one `confine_path(root, candidate) -> Path | ToolResult` helper (or a small context manager) returning the standard escape error.

### 1.3 — `[High] C14` The "task context unavailable" guard repeated across task/cron tools
`read_task_context(...) is None → _err("Background tasks are not available…")` (identical wording) appears in `task_get.py:39`, `task_output.py:44`, `task_stop.py:39`, plus `task_create`/cron/plan variants.
**Fix:** extract one `require_task_context(ctx) -> TaskSessionContext | ToolResult` guard.

### 1.4 — `[High] C14/D1` Path-canonicalization reimplemented 3× in the permissions layer
The "expanduser → anchor at cwd → normpath → `resolve(strict=False)` with OSError fallback" sequence is hand-rolled in `permissions/_checker.py:123` (`_path_forms`), `permissions/_credential_guard.py:47` (`_candidates`), and `permissions/_path_validator.py:15` (`_resolve`).
**Fix:** one shared `utils` helper `canonical_path_forms(path, cwd)`; all three call it.

### 1.5 — `[High] C14` `replace-step-by-id` ledger splice duplicated across sprint + tasks
The `list(ledger.steps)` → `enumerate`-find-by-id → `replace(step, …)` → `replace(ledger, steps=tuple(...))` / `KeyError` pattern recurs in `sprint/_outcome.py:32`, `sprint/_generator.py:41`, and `tasks/_ledger.py:186` (`append_note`/`mark_*`/`_replace_entry`).
**Fix:** one `_replace_step_by_id(ledger, step_id, mutate)` (and a sibling `_with_entry_replaced` for the ledger-entry tuple splice).

### 1.6 — `[High] C14` Task-id / path-traversal validator triplicated (with drift)
`planner/_artefacts.py:37 _checked_task_id`, `sprint/_checks.py:16 checked_task_id` (adds a `:` check), and `config/paths.py:58 _checked_task_id` are near-identical traversal validators — **already drifting** (the `:` rule exists in only one), which is exactly what C14 warns about.
**Fix:** consolidate into one `dream.utils` validator; keep the superset of checks.

### 1.7 — `[Med] C14` Smaller duplications worth a shared helper
- Process teardown (`terminate→wait→timeout→kill→cancel_waiter`) copy-pasted in `tasks/_manager.py:215` (`stop_task`) and `:251` (`restart_task`) → `async _terminate_process(task_id, process)`.
- One-shot completion self-unregister closure-dict in `services/cron.py:71` and `tasks/_cron_session.py:239` → shared `register_one_shot_completion(manager, task_id, fn)`.
- Depth-cap guard at the top of both `swarm/in_process.py:76` and `swarm/subprocess_backend.py:66` spawn methods → `_depth_guard(config, agent_id, backend_type) -> SpawnResult | None`.
- `read_all` / `drain` share the same scan in `swarm/_mailbox.py:237` → private `_scan() -> list[(path, msg)]`.
- TTY-gate + ANSI-wrap helpers (`_use_colour`/`_c`/`_flatten`) duplicated in `repl/_session.py:341` and `runner/_observer.py:103` → one shared ANSI module.
- `outcome → glyph` rule duplicated in `runner/_observer.py:287` and `:343` → `_outcome_glyph(outcome)`.
- `_env_int` / `_env_float` near-identical in `repl/_chat.py:88` → one `_env_number(name, default, parse, kind)`.
- POSIX/Windows lock scaffolding duplicated in `utils/file_lock.py:77/94/155/176` → one helper parameterized by blocking mode.
- `Literal` + matching validation-`frozenset` written twice: `swarm/_mailbox.py:45`, `sprint/_evaluation.py:34` → derive the set via `typing.get_args(...)`.

---

## 2. Monolith decompositions (Rule 4 / C6 / C19)

The biggest single-function and single-file offenders. Decompose into named steps so the parent reads as a plan; keep public signatures stable and lean on existing tests (TDD: characterize first).

| Sev | Location | Smell → Fix |
|---|---|---|
| **High** | `repl/_session.py:103` | `build_default_harness` ~200-line monolith (env parse, paths/task/cron bootstrap, policy warnings, capabilities, skills, 100-line nested `_factory`) → extract `_bootstrap_task_and_cron`, `_assemble_system_prompt`, and lift `_factory` to a module-level builder taking explicit deps. |
| **High** | `repl/_session.py:623` | `_handle_slash` ~165-line `if/elif` over 11 commands → a `_SLASH_COMMANDS` handler table (the sibling `_chat.py:934` already does exactly this). |
| **High** | `engine/_session.py:310` | `run_session` `while True` body ~175 lines, 6 phases → `_select_turn_driver`, `_maybe_compact`, `_drive_one_turn` (typed outcome), `_classify_turn_outcome`, `_check_abort_conditions`. |
| **High** | `services/compact/__init__.py:1` | 659-line god-module (microcompact + boundary + 8 attachment factories + checkpoints + PTL) → split into a package (`_microcompact`, `_boundary`, `_attachments`, `_checkpoints`, `_ptl`) re-exported via `__init__` + `__all__`. |
| **High** | `sprint/_negotiation.py:224` | `_run_negotiation_async` is a ~55-line near-clone of sync `_run_negotiation:165` differing only at two `await` seams → extract shared per-round entry-builders; let sync/async diverge only at the await. |
| **High** | `permissions/_checker.py:39` | `evaluate` 67-line 9-step pipeline inline → `_step_*(request, policy) -> Decision | None` helpers; `evaluate` becomes a short ordered scan returning the first non-None. |
| **High** | `engine/_tool_dispatch.py:104` | `dispatch` runs role-check→lookup→schema→gate→context→timeout-exec→offload→record inline with 5 exits → `_validate_input`, `_run_with_timeout`, `_offload_and_record`. |
| **High** | `engine/_adapter_openai.py:192` | `_consume` interleaves stream-accumulation and block-assembly → extract `_assemble_blocks(text_parts, tool_calls, usage)`. |
| **High** | `repl/_chat.py:673` | `_cmd_tool` ~90 lines (parse, json-decode, emit, asyncio.run, timeout, 3 render branches) → `_invoke_tool`, `_render_tool_result`, `_emit_tool_outcome`. |
| **High** | `runner/_run.py:183` | sprint-loop body ~165 lines (2a/2b/2c/2d) → `_run_generator_phase`, `_run_evaluator_phase` returning a `SprintRunResult`. |
| Med | `swarm/_worktree.py:164` | `create_worktree` ~60 lines (lock, fast-resume, `git worktree add`, symlink+meta) → `_resume_existing(...)` + `_create_fresh(...)`. |
| Med | `swarm/_worktree.py:248` | `list_worktrees` 20-line per-child body, consumed one-at-a-time by `cleanup_stale` → `_worktree_info_for(child)` + make it a generator (G1). |
| Med | `planner/_run.py:87` | `run_planner` ~60 lines (path math, lock/TOCTOU, LLM call, 2 atomic writes, events) → `_write_artefacts(...)` + `_build_events(...)`. |
| Med | `tools/builtin/bash.py:132` | `BashTool.execute` ~108 lines → `_resolve_cwd`, `_spawn`, `_run_with_timeout`, `_build_result`. |
| Med | `tools/builtin/git.py:189` | `GitTool.execute` validation cascade + run + assembly → `_validate(args) -> str | None` + `_build_result`. |
| Med | `engine/_session.py:163` | `_drive_turn_with_heartbeat` → extract the `asyncio.wait`-race into `_race_next_or_coma(...)`. |
| Med | `repl/_session.py:438` | `handle_event` 90-line `isinstance` ladder → type→renderer map with one `_render_*` per event. |
| Med | `services/threat_scan.py:233` | `_uses_dangerous` 4-branch AST walk → predicates `_is_eval_call`/`_imports_subprocess`/`_calls_subprocess_attr` + `any()`. |
| Med | `services/repo_validator.py:181` | `_validate_against_schema` 5 failures + mixed-level excepts → split schema-resolution from schema-application. |
| Med | `tools/_context.py:62` | `run_subprocess` ~85 lines → extract `_compose_subprocess_result(...)`. |

---

## 3. Show the shape (Rule 3 / A5 / C10) — `dict[str, Any]` without an example

Add a one-line example-shape comment (or a `TypedDict`) at each. Highest-value first because these are load-bearing contracts read by many callers.

| Sev | Location | What needs a shape comment |
|---|---|---|
| **High** | `tools/_base.py:227` | `ToolResult.metadata` — the load-bearing `dict[str,Any]` every tool builds by hand (`root_cause`/`safe_retry`/`stop_condition`/`returncode`/`summary`/`artifacts`). Document the recognized keys (or a `TypedDict`). |
| **High** | `services/compact/__init__.py:99,112` | `CompactAttachment.metadata` / `CompactionResult.metadata` + every `create_*_attachment_if_needed(metadata)` reads undocumented keys. |
| Med | `runner/_observer.py:204` | `on_event` + 19 `_on_*` handlers take `event: dict[str,Any]`; keys are fixed per kind → per-handler shape comment or a `TypedDict` per event kind. |
| Med | `engine/_adapter_openai.py:58,256,275` | `conversation_to_openai_messages` return, `_merge_tool_call` partial, `_usage_from_payload` payload — all opaque OpenAI wire dicts. |
| Med | `engine/_tool_dispatch.py:173` | `dispatch(input)` / `_permission_request(tool: Any, input)` — annotate `tool` as the `Tool` protocol; shape-comment `input`. |
| Med | `swarm/_registry.py:73,127` | `TeamMember.to_dict/from_dict`, `TeamFile.to_dict` — 14-field hand-rolled round-trips; add shape comment and consider `dataclasses.asdict` (as `_mailbox`/`_permissions` already do). |
| Med | `sprint/_contract.py:112` | `SprintContract.to_dict/from_dict` — nested on-disk JSON shape (verification_steps, negotiation_log). |
| Med | `swarm/_mailbox.py:80` | `MailboxMessage.payload` — type-dependent shape (user_message vs task_notification…). |
| Med | `swarm/_permissions.py:55` | `PermissionRequest.tool_input` — arbitrary tool-call arg map. |
| Med | `sprint/_negotiation.py:64` | `NegotiationResult.warning_event: dict[str,Any] | None`. |
| Med | `services/compact/_orchestrator.py:86` | `carryover_metadata: dict[str,Any]` threaded through 3 functions. |
| Med | `tools/builtin/mcp_tool.py:116` | `input_model_from_schema(schema: dict[str,object])` — add JSON-Schema example. |
| Med | `repl/_chat.py:179,235` | `_redact_args` shape; `SubstrateSpec.builder: Any` → `Callable[[Credential], Substrate]`. |
| Med | `state/sidecar.py:120` | `update_state(**changes: object)` hides the real signature → explicit keyword-only optionals matching `TaskState`. |
| Low | `verification/_report.py:28`, `mcp/_credentials.py:125`, `mcp/_allowlist.py:65`, `roles/_loader.py:38` | optional-key/ TOML-table shapes lacking an example. |

---

## 4. Dispatch ladders → tables/polymorphism (D2 / I4)

| Sev | Location | Smell → Fix |
|---|---|---|
| **High** | `swarm/_registry.py:275` | `get_executor` `if/elif backend == …/else` builder → `{BackendType: builder_callable}` dispatch table. |
| Med | `observability/_query.py:121` | `query_metrics` `if agg=="sum"/elif avg/else max` after validating against `_AGGREGATIONS` → `{"sum": sum, "avg": …, "max": max}` table. |
| Med | `config/from_env.py:56` | `default_auth_source_for_provider` growing per-provider `if` chain → provider→auth_source mapping, keep the `{provider}_api_key` fallback as code. |
| Med | `repl/_chat.py:819,771,880` | `_cmd_cron`/`_cmd_task`/`_cmd_plan` sub-command `if/elif` → per-command table-dispatch. |
| Low | `tasks/_cron_session.py:126` | `_derive_outcome` status switch → `match task.status` with explicit branches. |
| Low | `engine/credentials.py:156` | `record_attempt` 4-branch `if outcome == …` → borderline; reify to a `{outcome: handler}` map if `Outcome` grows. |

---

## 5. Middlemen & trivial wrappers (Rule 1 / Rule 2 / C11 / C15)

| Sev | Location | Smell → Fix |
|---|---|---|
| **High** | `sprint/_negotiation.py:165` | `_run_negotiation(await_results: bool)` — param the docstring admits is "always False" and never read → delete the dead param. |
| **High** | `sprint/_negotiation.py:143` | `negotiate_contract_async` builds a coroutine then `return await gen` — pure middleman → inline the async body. |
| Med | `harness.py:159` | `run_task` 90-line facade, 13 params, defaults 5 heads then forwards via hand-built `kwargs` dict → `_resolve_heads(...)` + real keyword args. |
| Med | `engine/_permission_gate.py:48` | `make_permission_gate` wraps `evaluate` in a 2-line closure only to bind `policy` → `functools.partial(evaluate, policy=policy)`. |
| Med | `permissions/_checker.py:152` | `_decide(outcome, reason, rule)` trivial 1-line forwarder → inline `PermissionDecision(...)` at call sites. |
| Med | `repl/_session.py:310` | `_build_context_metadata` 2-line dict-merge called once → inline. |
| Med | `repl/_session.py:683` | `/util`,`/compact` reach into `session._engine`/`engine.compactor` private attrs → add a `Session` accessor (stop the REPL being a middleman). |
| Med | `services/context_log.py:181` | `read_my_context_log = read_context_log` pure alias → drop or document. |
| Med | `tasks/_cron.py:163` | `_job_payload` trivial one-call wrapper used once → inline. |
| Low | `runner/_run.py:97` | `_default_goal` returns `step.description` → inline the fallback lambda. |
| Low | `config/paths.py:58`, `engine/_engine.py:70`, `repl/_chat.py:489` | one-line passthroughs / 1:1 config-laundering / private-attr reach → inline or add a public accessor. |

---

## 6. Exception handling (C1 / C3 / C5)

> Reminder: the fix is **narrow the except + structured return/raise** — *not* logging.

| Sev | Location | Smell → Fix |
|---|---|---|
| **High** | `engine/_session.py:410,417` | bare `except Exception` stringified into `turn_error`, mixing 3 abstraction levels (`ComaDetected`/`TimeoutError`/infra) → `_drive_one_turn` returns a typed outcome (`complete|timeout|coma|error`); narrow the catch to provider/transport types. |
| Med | `mcp/_client.py:110,119,152,166` | four `except Exception` where only SDK/transport errors are expected → narrow so unrelated bugs aren't masked as "server failed". |
| Med | `swarm/in_process.py:104`, `swarm/subprocess_backend.py:118` | `except BaseException` converts `CancelledError`/`KeyboardInterrupt` into a "failed" notification → narrow to `Exception` (or re-raise `CancelledError` after recording). |
| Med | `state/checkpoints.py:159` | `gc_checkpoints` runs `update-ref -d` unchecked, contradicting the file's "every git step checked" rule → wrap in `_checked` or return failed deletions as data. |
| Low | `repl/_session.py:1052` | `contextlib.suppress(CancelledError, Exception)` suppresses everything → drop the `Exception`. |
| Low | `api/openai.py:109` | identical bare-except + `_reraise_timeout` in `complete`/`stream` → extract `_call_translating_timeouts` wrapper (C14). |

---

## 7. Generators & misc idioms (G1 / B9 / C9 / A1)

| Sev | Location | Smell → Fix |
|---|---|---|
| Med | `services/compact/__init__.py:491` | `record_compact_checkpoint` mutates the passed-in `carryover_metadata` **and** returns it (C9 hidden side effect) → return a new dict or document the in-place contract. |
| Med | `services/cron.py:71` | `_record_completion_outcome` uses `dict[str,object]` closure cells as poor-man's `nonlocal` → use `nonlocal` flags or a tiny frozen state. |
| Low | `engine/_records.py:115` | `_ = field` placeholder keeps an unused import (A1) → drop it; re-add when a `default_factory` field lands. |
| Low | `engine/_session.py:209` | `return  # unreachable; appeases the type checker` (A2 what-comment) → `raise AssertionError(...)` or restructure. |
| Low | `config/from_file.py:214` | function-body `import os` → hoist to module scope. |
| Low | `services/tool_outputs.py:157` | 6 sequential `inline += …` concatenations → build a `list[str]` + `"\n".join`. |
| Low | `observability/_events.py:81` | six `*_attrs` builders repeat the omit-None pattern → `_with_optional(base, **opt)` helper. |
| Low | `read_offloaded.py:54`, `_mcp_effects.py:21` | redundant `..`/`is_absolute` pre-check duplicating `resolve_within` (C17); doubled tier fallback → collapse. |

---

## 8. Quick wins (Low effort, isolated — good warm-ups)

- Delete dead param `await_results` (`sprint/_negotiation.py:165`).
- Drop `_ = field` placeholder import (`engine/_records.py:115`).
- Hoist `import os` to module scope (`config/from_file.py:214`).
- `_outcome_glyph(outcome)` helper (`runner/_observer.py:287`/`:343`).
- Inline `_default_goal` / `_build_context_metadata` / `_job_payload` (trivial wrappers).
- Drop or document `read_my_context_log` alias (`services/context_log.py:181`).
- `SubstrateSpec.builder: Any` → `Callable[[Credential], Substrate]` (`repl/_chat.py:235`).

---

## 9. Audited and deliberately **not** flagged

So future passes don't re-litigate these:

- **No-logging** best-effort `contextlib.suppress(Exception)` cleanups and structured-fallback guards in `engine/_heartbeat.py:54`, `engine/_orientation.py:113`, `engine/_session.py` — intentional per the house rule.
- `execute(input: dict[str, Any], ctx)`, `ToolDeclaration`, `effects_for` — the deliberate `BaseTool` framework contract.
- `contracts/*` (tool.py, provider.py) — clean frozen-dataclass Protocols with example-shaped docstrings already present.
- `sandbox/`, `prompts/`, `plugins/`, `memory/`, `hooks/` — docstring-only stubs, no logic to refactor yet.
- `tasks/_fsm.py:34 _NEXT_STATE` lookup table — small/fixed; aligns with the "value on the enum member" preference only once a state class exists. Noted, not actioned.
- `swarm/_remote.py` always-`False` `shutdown` — gated YAGNI seam for the not-yet-present bridge; keep until the bridge lands.

---

*Method: 6 parallel `general-purpose` audit agents, disjoint package partitions, each reading the checklist + applying the 4 rules under the house constraints, returning verified `file:line` findings. Synthesized and de-duplicated here. No code was changed — this is a backlog.*
