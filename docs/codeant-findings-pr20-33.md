# CodeAnt AI Review Findings — PRs #20–#33

_63 findings across specs 04 (context/compaction), 05 (tools), 06.5 (background/heartbeat), 07 (exec-plans/tasks/cron)._

Severity: **Critical** 18, **Major** 44, **High** 1

Status legend: ⬜ todo · ✅ fixed · ⏭️ skipped (with reason)


## PR #20

### 1. [Critical] `src/dream/services/tool_outputs.py:194` ⬜
The traversal guard only blocks `..` segments, but absolute paths (for example `/etc/passwd`) contain no `..` and will still be read. This lets callers bypass the scratch-area boundary and read arbitrary files. Validate that the target path is under an expected scratch root (after `resolve()`), and reject absolute/out-of-root paths. [security]

### 2. [Major] `src/dream/services/context_log.py:132` ⬜
`payload["name"]` is used as a dict key without type validation, so malformed JSON like `{"name": []}` will raise an unhandled `TypeError` instead of a parse `ValueError`. Validate that `name` is a string before lookup (or catch `TypeError` and re-raise `ValueError`) to keep the decoder contract stable for malformed lines. [type error]

### 3. [Major] `src/dream/services/context_log.py:135` ⬜
Constructing the dataclass directly can raise `TypeError` when required fields are missing, but the function promises malformed input handling via parse errors. Wrap dataclass construction errors and re-raise `ValueError` so `read_context_log` callers can handle all malformed lines consistently. [api mismatch]

### 4. [Major] `tests/test_services/test_context_log.py:197` ⬜
This assertion only checks the subset of events matching one type, so the test still passes if unexpected extra events are appended to the file. That makes the append-behavior test too weak and allows regressions in log contents to slip through undetected. Assert the full event list (or exact line count plus contents) instead of filtering. [incomplete implementation]

### 5. [Major] `tests/test_services/test_context_log.py:215` ⬜
`writer.close()` is executed only after the assertion, so if that assertion fails the file handle remains open. On platforms with strict file locking (notably Windows), this can leak the handle and cause temp-directory cleanup failures that cascade into unrelated test failures. Close the writer in a `finally` block (or use a context manager) so cleanup always runs. [resource leak]


## PR #21

### 6. [Critical] `src/dream/services/compact/__init__.py:210` ⬜
`truncate_head_for_ptl_retry` returns `retained` messages without re-sanitizing, so if the retained tail ends with an orphan assistant `ToolUseBlock` (common after interrupted turns), the next provider call can be rejected as an invalid transcript. Run `sanitize_conversation_messages` on the retained output (after optional marker insertion) before returning. [api mismatch]

### 7. [Major] `src/dream/services/compact/__init__.py:136` ⬜
`collect_compactable_tool_ids` marks every result from canonical tools as compactable even when the payload is tiny, but `microcompact_messages` replaces it with a long sentinel string. For short outputs this makes the transcript larger (negative compaction) while still reporting positive savings. Gate canonical-tool compaction by comparing against sentinel size (or applying a minimum-size threshold) before clearing. [logic error]

### 8. [Major] `src/dream/services/compact/__init__.py:229` ⬜
`split_preserving_tool_pairs` does not validate `preserve_recent`, so a negative value makes `split_index` exceed the list bounds and `messages[split_index]` raises `IndexError`. Clamp `preserve_recent` to a non-negative range before computing `split_index`. [logic error]


## PR #22

### 9. [Critical] `src/dream/services/compact/_orchestrator.py:162` ⬜
This branch builds a `CompactionResult` that includes boundary and attachments, but returns raw `microcompacted` messages instead of the rebuilt post-compact transcript. That creates a tier-dependent contract mismatch where full tier returns rebuilt messages while micro tier drops boundary/attachments unless every caller manually rebuilds. [api mismatch]

### 10. [Major] `src/dream/services/skill_disclosure.py:117` ⬜
The registry bootstrap currently reads and parses every full `SKILL.md` body at session start, which violates the progressive-disclosure contract and scales startup cost with total skill body size. Keep startup parsing to frontmatter only (stop at closing `---`) and defer body reads strictly to `use_skill`. [performance]

### 11. [Major] `src/dream/services/compact/_orchestrator.py:192` ⬜
The boundary metadata keys passed here do not match what `create_compact_boundary_message` consumes, so pre-compaction footprint details are silently omitted from the boundary marker. Use the expected keys (`pre_compact_message_count` / `pre_compact_token_count`) so the marker contains the intended recovery data. [api mismatch]


## PR #23

### 12. [Critical] `src/dream/tools/_base.py:205` ⬜
`input_model` is required to be a `pydantic.BaseModel` subclass, but `__init_subclass__` never validates that contract. A tool can pass class creation with a non-model `input_model` and then crash when `input_schema()` calls `model_json_schema()`. Add explicit type validation for `input_model` during subclass creation to keep this import-time blocking as intended. [type error]

### 13. [Critical] `src/dream/tools/_context.py:67` ⬜
`run_subprocess` promises to return a `ToolResult`, but process creation errors (for example missing executable or invalid `cwd`) are not caught and will raise directly. That breaks the tool contract and can crash the call path instead of returning a structured tool error. Catch spawn-time `OSError`/`FileNotFoundError` and convert it into an `is_error=True` `ToolResult` with recovery metadata. [api mismatch]

### 14. [Major] `src/dream/tools/_base.py:192` ⬜
The class-validation gate can be bypassed for concrete subclasses that inherit `execute` from a parent, because validation is skipped whenever `execute` is not defined in the subclass `__dict__`. That allows subclasses with missing/invalid declaration fields to load without `ToolDeclarationError`, then fail later at runtime (for example in `is_read_only()` or schema generation). Use an abstractness check (e.g., skip only truly abstract classes) instead of checking for `execute` in `__dict__`. [incomplete implementation]

### 15. [Major] `src/dream/tools/_context.py:83` ⬜
There is a timeout race where the child process can exit just before `kill()` runs, causing `kill()` to raise `ProcessLookupError` and bypass the intended timeout `ToolResult`. Make timeout cleanup resilient by suppressing already-exited-process errors before waiting, so timeout handling always returns the structured error. [race condition]


## PR #24

### 16. [Critical] `src/dream/tools/builtin/git.py:54` ⬜
** Architect Review — CRITICAL** GitTool is declared risk="safe" and documented as read-only, but the ALLOWED_SUBCOMMANDS set includes subcommands like "config", "remote", and "stash" and accepts arbitrary trailing arguments, so the tool can perform mutating git operations (e.g. `git config --global ...`, `git remote add ...`, `git stash drop`), violating the "never mutates external state" contract for safe tools. — Suggested fix: Restrict the allowlist to truly read-only subcommands or reclassify GitTool as mutating, and add per-subcommand argument validation to block mutating forms such as `git config --global ...`, `git remote add ...`, and `git stash push/pop/drop`.

### 17. [Critical] `src/dream/tools/builtin/git.py:55` ⬜
The tool is declared as safe/read-only, but the allowlist includes subcommands that can mutate repository or user state (for example `git config --global`, `git branch <name>`, `git tag <name>`, `git remote add`, `git stash`). This creates a permission bypass where a tier-0 "safe" tool can still perform writes. Restrict arguments to read-only forms or remove mutating-capable subcommands from the allowlist. [security]

### 18. [Critical] `src/dream/tools/builtin/read_offloaded.py:60` ⬜
Path validation only checks for `..` and absolute input, but does not resolve and verify the final target stays under `scratch_dir`; a symlink inside scratch can point outside and be read. Resolve the final path and enforce it is a descendant of scratch before reading. [security]

### 19. [Critical] `src/dream/tools/builtin/bash.py:60` ⬜
Treating any command whose first token is `git` as read-only is unsafe because mutating git operations (`git commit`, `git reset --hard`, `git clean`, etc.) will be downclassified as safe. Restrict read-only classification to a vetted allowlist of git subcommands (like the dedicated `git` tool does) instead of all `git` invocations. [security]

### 20. [Critical] `src/dream/tools/builtin/file_read.py:90` ⬜
Path resolution allows absolute paths and `..` traversal to resolve outside the working directory, so this tool can read arbitrary host files instead of only repository files. Enforce that resolved paths stay within `ctx.working_dir` (or a sandbox-approved root) before reading. [security]

### 21. [Critical] `src/dream/tools/builtin/file_write.py:59` ⬜
Write failures from `atomic_write_text` (permissions, disk full, invalid path, etc.) are not converted into a `ToolResult` error with the required metadata contract, so they raise and become generic infrastructure failures upstream. Catch filesystem exceptions and return structured tool errors with `root_cause`, `safe_retry`, and `stop_condition`. [api mismatch]

### 22. [Critical] `src/dream/tools/builtin/file_write.py:66` ⬜
The write path is resolved without confinement to the working directory, allowing writes to arbitrary filesystem locations via absolute paths or traversal segments. Add a post-resolve containment check and reject paths outside the allowed root. [security]

### 23. [Major] `src/dream/tools/builtin/file_edit.py:66` ⬜
Reading the file with strict UTF-8 decoding can raise `UnicodeDecodeError` on non-UTF8 content, and this path is not handled, so the tool can fail with an exception instead of returning a ToolResult error contract. Decode with replacement or catch decode errors and return `_err`. [possible bug]

### 24. [Major] `src/dream/tools/builtin/file_edit.py:81` ⬜
Empty `old_str` is not rejected; `str.count("")` returns `len(text)+1` and replacement inserts text at boundaries, causing unintended edits and misleading occurrence metadata. Reject empty search strings explicitly before counting/replacing. [logic error]

### 25. [Major] `src/dream/tools/builtin/read_offloaded.py:77` ⬜
The code only catches `ValueError` from `read_offloaded`, so directory paths or permission failures (e.g., `IsADirectoryError`, `PermissionError`) will escape as uncaught exceptions instead of returning a structured tool error. Add explicit file-type checks or broaden exception handling for filesystem errors. [possible bug]

### 26. [Major] `src/dream/tools/builtin/bash.py:136` ⬜
The process is awaited before stdout is consumed, which can deadlock when a command writes more than the pipe buffer (the child blocks on write, never exits, and then gets reported as a timeout). Read stdout/stderr concurrently with process execution (for example via `communicate()` or a background reader task) instead of waiting for process exit first. [logic error]

### 27. [Major] `src/dream/tools/builtin/file_read.py:67` ⬜
The implementation reads the entire file into memory before applying `offset`/`limit`, so a very large file can cause excessive memory use or OOM despite requesting only a small slice. Stream or iterate lines and stop after the requested range instead of loading full bytes first. [performance]


## PR #25

### 28. [Critical] `src/dream/repl/_chat.py:635` ⬜
Emitting `args` verbatim into `tool.invoked` logs can leak sensitive data (for example file contents passed to `write_file` or secrets embedded in `bash` commands) into the persistent JSONL event file. Redact or omit high-risk fields before emitting tool invocation events. [security]

### 29. [Major] `src/dream/repl/_chat.py:638` ⬜
Tool execution bypasses the declared per-tool timeout (`declaration.timeout_seconds`), so a long-running or hung tool can block the REPL loop indefinitely. Wrap execution with a timeout based on the tool declaration and convert timeout failures into the existing `tool.failed` path. [incomplete implementation]


## PR #26

### 30. [Major] `src/dream/engine/_tool_dispatch.py:108` ⬜
Catching bare `TimeoutError` around `wait_for` also swallows `TimeoutError` raised by the tool itself, incorrectly converting a tool exception into a synthetic timeout result. This violates the stated passthrough behavior for tool exceptions and can hide real failures. Distinguish `wait_for` deadline expiry from inner tool exceptions (for example by awaiting a created task and checking task state/exception before mapping to timeout). [logic error]


## PR #27

### 31. [Major] `tests/test_session.py:152` ⬜
The final ordering assertion is logically ineffective: it compares the index of the first `TurnComplete` with the index of the second `TurnComplete`, which is always true once you already asserted there are two completes. This does not validate the intended contract that the first completion happens before the second turn's text output, so a real event-order regression could pass this test undetected. [incomplete implementation]

### 32. [Major] `tests/test_session.py:228` ⬜
This test claims to verify that both the prior assistant `tool_use` and matching user `tool_result` are preserved across sends, but it only validates the tool-result message and never asserts that the assistant tool-use message is present. That leaves the core "tool-use + tool-result atom" contract partially untested, so regressions that drop the assistant tool-use block could still pass. [incomplete implementation]

### 33. [High] `src/dream/session.py:223` ⬜
** Architect Review — HIGH** The session transcript currently mirrors ToolExecutionCompleted.result verbatim, so when the dispatcher raises and run_query emits a detailed internal error string on the event stream (while writing a generic "tool failed to execute" marker into the transcript), Session reintroduces those internal exception details into its persisted transcript and into the resume_messages passed back into run_session, violating the engine's non-leakage guarantee. — Suggested fix: Keep Session's transcript reconstruction aligned with run_query's transcript contract: either reuse the canonical transcript ToolResultBlock content from the engine, or enrich ToolExecutionCompleted so Session can distinguish infra failures from tool-level errors and write the same generic, non-revealing marker the engine uses into its transcript.

### 34. [Major] `src/dream/session.py:139` ⬜
`Session` stores per-call cancellation state in shared instance fields, so concurrent `send` calls overwrite each other's `_cancel_event`/`_inner`. A later `send` can make `cancel()` target the wrong stream, and one call's `finally` can null out state for another still-running call. Add a single-flight guard (reject concurrent `send`) or track cancellation state per active stream instead of one shared slot. [race condition]

### 35. [Major] `src/dream/session.py:200` ⬜
The final transcript flush for accumulated `ToolResultBlock`s is skipped when the stream was cancelled, but cancellation can happen after tool results were received and before the next assistant completion. That leaves a dangling assistant tool-use in transcript history and causes resume sanitization to drop prior context. Flush pending tool results whenever they exist, including the cancelled path. [incorrect condition logic]


## PR #28

### 36. [Major] `src/dream/repl/__main__.py:126` ⬜
`--max-turns` accepts any integer, including `0` or negatives, which can make the engine perform zero assistant turns and silently return no response. Add a positive-integer constraint at argument parsing time (or validate before building session options) so invalid turn caps fail fast instead of producing confusing no-output behavior. [logic error]

### 37. [Major] `src/dream/repl/_session.py:236` ⬜
The `/compact` command forces compaction with `force=True`, but then checks `if result is None` as if a no-op can happen. With the current orchestrator contract (`force=True` and no summariser), this branch is effectively unreachable, so `/compact` reports a compaction-completed event even when nothing was actually compacted. Remove the unconditional force path or explicitly compare pre/post transcript/token deltas to detect real no-op compactions. [incorrect condition logic]

### 38. [Major] `src/dream/repl/_session.py:369` ⬜
The stop lifecycle event is emitted only after `asyncio.run(...)` returns successfully; if startup or session execution raises, `"session.repl.stopped"` is never written. Wrap execution in `try/finally` so stop events are emitted consistently and downstream watchers don't see a dangling started-without-stopped lifecycle. [missing cleanup]

### 39. [Major] `tests/test_repl/test_session_repl.py:104` ⬜
The assertion uses `or`, so it passes even if the error message only mentions one missing required variable. This can hide regressions where `build_default_harness` stops reporting all missing required env keys. Require both required keys in the assertion so the test actually validates the full contract. [incorrect condition logic]

### 40. [Major] `tests/test_repl/test_session_repl.py:254` ⬜
This test claims `/quit` should not trigger a send, but it only checks that the startup banner was printed, so it can still pass even if `/quit` is incorrectly sent to the model and fails internally. Add an assertion on observable send behavior (for example, zero streamer calls or absence of `turn_failed`) to prevent false positives. [incomplete implementation]

### 41. [Major] `src/dream/session.py:266` ⬜
`CompactionDoneEvent` is translated to a public event but the session's persisted `_transcript` is never updated to the compacted transcript shape, so the next `send()` resumes from stale, un-compacted history. This breaks cross-send consistency (compaction appears to happen, but old messages still get re-sent later). Include the compacted transcript (or a deterministic mutation payload) in the internal event and apply it to `_transcript` when handling compaction. [incomplete implementation]


## PR #29

### 42. [Critical] `src/dream/wake/_runner.py:130` ⬜
The turn stream is consumed with a plain `async for` and then broken on the first `AssistantTurnComplete`, but the iterator is never explicitly closed. In this codebase, `TurnStreamer` implementations can own transport resources and are normally wrapped with `contextlib.aclosing`; without that, early exit/cancellation can leave the underlying stream open and leak connections. [missing cleanup]

### 43. [Major] `src/dream/wake/__init__.py:36` ⬜
The package-level API omits `BUNDLED_HEARTBEAT_PROMPT` even though this slice's stated public surface includes it; callers using `from dream.wake import BUNDLED_HEARTBEAT_PROMPT` will fail at import time. Re-export the constant from `dream.wake._prompt` and include it in `__all__` so the documented surface matches the actual module contract. [api mismatch]

### 44. [Major] `src/dream/wake/_tool.py:57` ⬜
The per-task 200-char rule is enforced only in `model_post_init`, so it is not represented in the published JSON schema for the tool and the model is not guided to stay within that limit. This creates a contract mismatch where inputs that appear schema-valid to the provider are later rejected at runtime, producing avoidable missing/invalid heartbeat decisions. Define the task item type with a built-in max-length constraint so the same rule is enforced and advertised in schema. [api mismatch]

### 45. [Major] `src/dream/wake/_runner.py:53` ⬜
The wake prompt is trimmed with `rstrip()` before being sent, which removes trailing whitespace/newlines from operator overrides. That breaks the stated "verbatim/no trim" behavior for override prompts and can silently alter operator-authored prompt formatting. [logic error]

### 46. [Major] `src/dream/wake/_runner.py:121` ⬜
The decision timestamp is taken before the model turn runs, so `decided_at` records request-start time rather than when the decision was actually produced. On slow provider responses this skews audit timing and any downstream wake scheduling logic that relies on accurate decision time. [logic error]


## PR #30

### 47. [Critical] `src/dream/wake/_state.py:56` ⬜
`read_state` promises to return defaults on any read failure, but it only handles `FileNotFoundError` when reading the file. Other real read failures (for example `PermissionError`, transient I/O errors, or decode failures while reading text) will currently propagate and can crash the wake cycle instead of falling back to default state. Broaden the read exception handling to cover non-parse read errors and keep the function's forgiving contract. [incomplete implementation]

### 48. [Critical] `src/dream/wake/_orchestrator.py:84` ⬜
The event callback is invoked without isolation, so any exception raised by the observer will abort the wake cycle after the decision/state update has already happened. That creates a partial-commit failure mode where callers see an exception and may retry, producing duplicate decisions. Wrap callback failures (or explicitly sandbox them) so observability hooks cannot break orchestrator control flow. [logic error]

### 49. [Major] `src/dream/wake/_decision.py:89` ⬜
`wake_source` type validation is too permissive: any non-dict value is silently coerced to `None`, so malformed/corrupted records are accepted as valid and lose source provenance. This breaks the parser's stated "shape errors become ValueError" contract and can hide audit-trail data corruption. Treat non-`None`, non-dict `wake_source` values as malformed and raise `ValueError` instead of defaulting to `None`. [logic error]

### 50. [Major] `src/dream/wake/_source.py:110` ⬜
`idle_minutes` is coerced with `int(...)`, which silently accepts non-integer values (for example booleans and floats) and can truncate data instead of rejecting malformed records. This can corrupt replayed wake metadata; validate that the field is already an integer and raise on invalid types instead of coercing. [type error]


## PR #31

### 51. [Critical] `src/dream/tasks/_plan.py:130` ⬜
`task_id` is used directly to build filesystem paths without validating path separators or absolute paths. A crafted value like `../...` can escape the plan directory and read arbitrary files. Validate `task_id` against the same safe-segment rules used elsewhere before joining it into paths. [security]

### 52. [Major] `src/dream/tasks/_ledger.py:138` ⬜
Entry lookup is by `id`, but duplicate entry IDs are currently allowed, so updates like `append_note`/`mark_done` can silently target only the first matching item and leave the rest inconsistent. Add a ledger invariant that rejects duplicate `entries[].id` at construction/validation time. [logic error]

### 53. [Major] `src/dream/tasks/_ledger.py:148` ⬜
`append_note` mutates ledger content but does not update `updated_at`, so consumers relying on that timestamp will see stale metadata after note changes. Update `updated_at` in this mutation path the same way status transition helpers do. [incomplete implementation]

### 54. [Major] `src/dream/tasks/_tech_debt.py:59` ⬜
The formatter claims to emit a single-line bullet, but `missing`, `evidence`, and `task_id` are not constrained to single-line values. If any field contains `\n`, one entry can expand into multiple Markdown lines and break the tracker format (or inject fake bullets/sections). Enforce newline-free fields in model validation before rendering. [security]

### 55. [Major] `src/dream/tasks/_tech_debt.py:94` ⬜
`append_tech_debt_entry` does a read-modify-write without any inter-process locking, so concurrent writers can overwrite each other and lose previously appended bullets. Add a file lock (or another serialization mechanism) around the read + append + write critical section to make appends durable under concurrent runs. [race condition]

### 56. [Major] `src/dream/tasks/_fsm.py:123` ⬜
The code uses `<= cutoff` while the function contract says plans must be "older than" `retention_days`. This incorrectly includes plans exactly on the boundary day. Use a strict comparison so only truly older plans are archived. [off-by-one]

### 57. [Major] `src/dream/tasks/_plan.py:137` ⬜
`read_plan` never checks that the loaded ledger belongs to the requested `task_id`. If the JSON file contains a different `task_id`, callers asking for one task can silently get another task object, which can misroute later writes/moves. Enforce `ledger.task_id == task_id` and fail when they differ. [api mismatch]


## PR #32

### 58. [Critical] `src/dream/tasks/_seam.py:85` ⬜
This listener does a read-modify-write on the ledger file with no synchronization, so two task completions targeting the same `ledger_path` can race and overwrite each other's updates (lost notes/status updates). Add per-ledger locking (or file-locking) around the whole read/update/write transaction to make updates atomic across concurrent completions. [race condition]

### 59. [Major] `src/dream/tasks/_types.py:63` ⬜
The dataclass is declared frozen, but it still exposes mutable containers (`metadata`, `env`, `argv`), so callers can mutate task state in place after retrieval and bypass the intended immutable transition helpers. Use immutable container types (e.g., mapping proxy/tuples) or deep-copy and hide mutable internals to preserve the immutability contract. [possible bug]

### 60. [Major] `src/dream/tasks/_manager.py:130` ⬜
`create_shell_task` stores the task as `running` before subprocess creation succeeds. If `create_subprocess_exec/shell` fails (for example invalid executable/cwd), the method raises but leaves a ghost task in `_tasks` with no process, so later `stop_task`/`list_tasks` report inconsistent state. Only publish the task to manager state after `_start_process` succeeds, or roll back `_tasks`/`_output_locks` on startup failure. [state inconsistency]

### 61. [Major] `src/dream/tasks/_manager.py:181` ⬜
`stop_task` adds the task id to `_suppress_watcher_notify` and then calls `process.terminate()` without a `try/finally`; if termination raises (notably `ProcessLookupError` when the process already exited), suppression is never cleared and future watcher completions for that task id can be silently dropped. Wrap suppression lifecycle in `try/finally` so cleanup always happens. [race condition]

### 62. [Major] `src/dream/tasks/_manager.py:221` ⬜
On restart, the record is only changed to `running` but prior run lifecycle fields are preserved. That means a running task can still carry old `ended_at`/`return_code` (and stale `started_at`), which breaks lifecycle semantics for callers that treat these fields as current-run data. Rebind with fresh start timestamp and clear terminal fields before spawning the new process. [logic error]

### 63. [Major] `src/dream/tasks/_manager.py:341` ⬜
Listener contract allows any `Awaitable`, but notification code only awaits coroutine objects via `asyncio.iscoroutine`. Awaitable non-coroutines (for example `Future`-returning listeners) will be treated as sync and never awaited, causing listener work to be skipped and producing un-awaited awaitable warnings. Use an awaitable check compatible with the declared contract. [api mismatch]
