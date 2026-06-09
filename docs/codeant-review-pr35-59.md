# CodeAnt review findings — PRs #35–#59 (with resolution status)

_Generated 2026-06-08 from `codeant-ai[bot]` inline comments. 95 unique findings across 22 PRs (8 Architect-Review). Fixed on branch `chore/codeant-fixes-35-59` via a 6-agent parallel TDD pass; full suite 2356 passing, ruff + mypy clean._

## Resolution status

| Status | Count |
|---|---|
| ✅ Fixed | 92 |
| ➖ Already fixed | 3 |

**Total: 95** — all resolved (92 fixed this pass + 3 already addressed in the tree).

- **✅ Fixed** — patched TDD-style (test reproducing the issue + minimal fix + clean refactor).
- **➖ Already fixed** — the tree already addressed it (earlier 13E/13F work); no change needed.

## ⭐ Architect-Review findings (design-level)

| Status | Sev | PR | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | **Critical** | #39 | `src/dream/tasks/_ledger.py`:137 | The new claim field is added to the Ledger model and is always serialized (as null when unset), but the checked-in JSON Schema docs/_schemas/exec-plan-ledger.schema.json still has additionalProperties: false and no claim property; exec-plan |
| ✅ Fixed | **Critical** | #47 | `src/dream/engine/_tool_dispatch.py`:121 | When a permission_gate is wired, the PermissionRequest is built solely from tool.effects_for(...), but several mutating built-in tools (e.g. task_create, task_stop, mcp_auth and MCP adapters) still use the default empty ToolEffects, so eval |
| ✅ Fixed | **High** | #43 | `src/dream/tools/builtin/task_get.py`:54 | Unknown-id error guidance for task tools tells callers to "call task_list" to discover valid ids, but no task_list tool exists in the default registry or anywhere in the codebase, so the documented recovery path is impossible to follow. |
| ✅ Fixed | **High** | #44 | `src/dream/repl/_runtime_info.py`:30 | The runtime-info block reports POSIX shells using $SHELL (via detect_shell), but task_create command=... is actually executed with asyncio.create_subprocess_shell without an explicit executable, which always invokes /bin/sh on POSIX; this c |
| ✅ Fixed | **High** | #49 | `src/dream/services/threat_scan.py`:97 | Path‑glob suppressions compare glob_to_regex patterns (which are POSIX "/"-based) against Finding.path strings built from Path.__str__, so on Windows (where these contain backslashes) valid ignore globs like "tests/" will not suppress findi |
| ✅ Fixed | **High** | #51 | `src/dream/services/cron.py`:182 | run_cron_kind bases enabled/disabled solely on the manifest's enabled flag and ignores the registry job's enabled state, so a job disabled via registry workflows (e.g. /cron toggle) will still be executed when fired via python -m dream.repl |
| ✅ Fixed | **High** | #51 | `src/dream/repl/_cron_cli.py`:60 | The cron CLI bootstraps manifest files but never seeds the registry from those manifests, so first-run or CLI-only cron setups can execute jobs while leaving the registry (and /cron list/cron_show state) empty and without last_run/last_stat |
| ✅ Fixed | **High** | #54 | `src/dream/swarm/subprocess_backend.py`:132 | SubprocessExecutor registers a completion listener for each spawn and only unregisters it on the exception path; on successful spawns the listener is never unregistered, so BackgroundTaskManager._listeners grows without bound and every late |

## PR #35 — feat(spec-06/slice-1): skills catalogue + progressive disclosure

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/skills/_frontmatter.py`:34 | Frontmatter parsing is inconsistent between eager metadata load and lazy body load: _read_header_only accepts closing fences with surrounding whitespace, but split_frontmatter requires an exact --- line. A skill file can successfully regist |
| ✅ Fixed | Low | Inline | `src/dream/skills/_registry.py`:30 | Collision detection is case-sensitive, but resolution is case-insensitive, so registering names that differ only by case can silently create two entries with no shadow record while lookups always resolve to just one of them. Detect collisio |
| ✅ Fixed | Low | Inline | `src/dream/skills/_registry.py`:41 | Re-registering a skill with the same canonical name does not remove lookup keys (aliases/command names) from the shadowed definition, so stale aliases keep resolving to the new skill even when the new definition never declared them. This cr |

## PR #36 — feat(spec-06/slice-2): skill tool + enforcement + session validation …

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/skills/_validate.py`:46 | The validator only catches SkillFrontmatterError, but read_skill_meta can also raise OSError/UnicodeDecodeError when a skill file is unreadable or not valid UTF-8. In that case session startup will crash instead of returning a blocking find |
| ✅ Fixed | Possible-bug | Inline | `src/dream/repl/_session.py`:355 | /skill directly calls registry.use_skill(...) without handling load failures. If the skill file is removed/corrupted after startup (or cannot be read), use_skill will raise and crash the REPL command path instead of returning a user-facing  |
| ✅ Fixed | Low | Inline | `src/dream/repl/_session.py`:626 | When a harness is injected and working_dir is omitted, this code validates and builds skills from Path.cwd() instead of the injected harness's configured repo root. That can block a valid injected harness because of unrelated skills in the  |

## PR #37 — feat(spec-06/slice-3): MCP allowlist admission + client manager + too…

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/mcp/_client.py`:187 | Repeated calls to connect can leak live sessions because the new session/stack replaces any existing entry without first closing the previous stack. If connect_all() is called more than once (without close()/reconnect_all()), old transports |
| ✅ Fixed | Low | Inline | `tests/test_mcp/test_client.py`:83 | This test claims to validate unsupported transport handling, but it still creates a stdio entry and only triggers a missing fake-server error path. That means unsupported transport behavior is not actually exercised, so regressions in trans |
| ✅ Fixed | Low | Inline | `tests/test_tools/test_builtin/test_mcp_tool.py`:39 | _connected_manager returns an already-connected McpClientManager but never ensures it is closed, and the tests that call it also do not close it. This leaks live in-memory ClientSession/exit stacks across tests and can cause flaky async tea |
| ✅ Fixed | Low | Inline | `src/dream/mcp/_allowlist.py`:95 | The parser accepts any list contents for tools and coerces each value to string instead of validating list[str]. Malformed entries (e.g., numbers/objects) are silently accepted and then fail matching at runtime, causing confusing missing-to |
| ✅ Fixed | Low | Inline | `src/dream/mcp/_client.py`:71 | Building _entries as a dict silently drops earlier entries when duplicate server names exist in the allowlist. That makes admission/config behavior order-dependent and can unintentionally replace a stricter entry with a later one. [logic er |
| ✅ Fixed | Low | Inline | `src/dream/tools/builtin/mcp_tool.py`:114 | Optional JSON Schema properties are being modeled as nullable (type \| None), but in JSON Schema "not required" does not imply null is valid. This makes the adapter accept payloads with explicit nulls that violate the server schema and can f |
| ✅ Fixed | Low | Inline | `src/dream/tools/builtin/mcp_tool.py`:122 | MCP tool registration ignores per-entry tier policy and hardcodes every adapter to tier 1, which breaks the allowlist contract and can mis-gate tools (either too permissive or too restrictive). Derive tier_required from the server's Allowli |
| ✅ Fixed | Suggestion | Inline | `tests/test_tools/test_builtin/test_mcp_tool.py`:51 | Catching Exception here is too broad and can make the test pass for unrelated failures (e.g., model creation/runtime errors) instead of specifically verifying required-field validation behavior. Assert the concrete validation exception type |
| ✅ Fixed | Suggestion | Inline | `src/dream/mcp/_client.py`:92 | close() clears only live session/stack maps but leaves per-server status objects untouched, so servers can still appear connected and keep old tool/resource inventories after shutdown. This creates inconsistent state and can cause callers t |

## PR #38 — feat(spec-06/slice-4): MCP resources, auth, transports, REPL wiring

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/tools/builtin/mcp_auth.py`:115 | The reconnect result is ignored, so blocking findings from reconnect_all() (for example version-pin mismatch on another server) are silently dropped and this tool can report success anyway. Capture the returned findings and fail when any bl |
| ✅ Fixed | Critical | Inline | `src/dream/repl/_session.py`:723 | setup_mcp_session(...) is awaited without handling registration-time exceptions (for example ToolCollisionError when MCP tool names sanitize to an existing name). In that failure path, the exception escapes and aborts the REPL instead of re |
| ✅ Fixed | Low | Inline | `src/dream/repl/_mcp.py`:90 | Tool registration errors are not handled, so a name collision (which ToolRegistry.register raises as ToolCollisionError) will crash setup despite the function contract saying it does not raise on bad input. Wrap registration in error handli |
| ✅ Fixed | Suggestion | Inline | `tests/test_utils/test_fs.py`:96 | This test enforces POSIX permission bits unconditionally, but Windows does not preserve/report st_mode in the same way, so this assertion can fail on Windows CI despite correct behavior. Add a platform guard (like the existing skipif(sys.pl |

## PR #39 — Spec08/task claim and lease

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/coordination/_claim.py`:157 | Catching all sqlite3.OperationalError and converting them to False masks serious database failures (corruption, I/O, schema problems) as if they were just missed heartbeats. Restrict this handling to lock/busy cases and let non-transient da |
| ✅ Fixed | Critical | Inline | `src/dream/coordination/_claim.py`:170 | The release path can raise unexpectedly after the board update because mirror writes are not protected. If on_release fails, the method throws instead of returning True/False, even though ownership tokens were already cleared in the board,  |
| ✅ Fixed | Critical | Inline | `src/dream/tasks/_ledger.py`:137 | Adding the new claim field to the ledger model without updating docs/_schemas/exec-plan-ledger.schema.json breaks the declared $schema contract for written ledger JSON. Any validator that checks ledger files against that schema will now rej |
| ✅ Fixed | Critical | Architect | `src/dream/tasks/_ledger.py`:137 | The new claim field is added to the Ledger model and is always serialized (as null when unset), but the checked-in JSON Schema docs/_schemas/exec-plan-ledger.schema.json still has additionalProperties: false and no claim property; exec-plan |
| ✅ Fixed | Possible-bug | Inline | `src/dream/coordination/_board.py`:141 | If commit itself fails, the transaction manager does not attempt rollback, which can leave the connection in an open transaction state and break subsequent operations with nested-transaction errors. Add rollback handling around commit failu |
| ✅ Fixed | Suggestion | Inline | `tests/test_utils/test_clock.py`:16 | This assertion is flaky because it compares SystemClock().now_ms() against two separate wall-clock reads that can move backward/forward due to NTP or VM clock adjustments. In those cases now can fall outside [before, after] even when System |
| ✅ Fixed | Suggestion | Inline | `tests/test_utils/test_clock.py`:23 | This test assumes wall-clock time never goes backward between two consecutive calls, but SystemClock is backed by time.time() which is not monotonic and can decrease after clock sync events. That makes the test intermittently fail in CI. Us |

## PR #41 — feat(spec-12b): agent-queryable observability (query_logs + query_metrics)

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/observability/_query.py`:51 | read_events claims malformed lines are skipped, but it only catches ValueError and KeyError. Valid JSON lines with the wrong top-level type (for example [] or "x") will raise TypeError inside from_jsonl_line and crash the query path instead |
| ✅ Fixed | Low | Inline | `src/dream/observability/_query.py`:133 | Duration parsing accepts signed numeric bodies, so an invalid input like --1h is parsed as -1 hours and turns into a future timestamp (now - (-1h)). The relative syntax should require an unsigned positive integer body after the single leadi |
| ✅ Fixed | Low | Inline | `src/dream/tools/builtin/observability_query.py`:78 | The until field is documented as defaulting to now, but when until is omitted this code passes None through, removing the upper bound entirely. That breaks the documented contract and can include future-dated events; set until_ms to now whe |
| ✅ Fixed | Suggestion | Inline | `src/dream/tools/builtin/observability_query.py`:111 | query_metrics has the same default-window bug as query_logs: missing until becomes None instead of now, so aggregation can include future timestamps and diverges from the declared API behavior. Apply now as the default upper bound when unti |

## PR #42 — feat(spec-12c): verify-like-a-user runner + report

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/verification/_config.py`:43 | The config loader treats any non-file path as "missing config" and silently returns no verification steps. If .harness/verification.toml exists but is a directory (or another non-regular file), verification is incorrectly skipped instead of |
| ✅ Fixed | Critical | Inline | `src/dream/verification/_runner.py`:42 | Exceptions raised by ui_verifier.verify() are not handled, so one UI verification failure crashes the whole run instead of producing an error step and continuing. Catch verifier exceptions per path and append an error result to keep report  |
| ✅ Fixed | Critical | Inline | `src/dream/verification/_runner.py`:89 | Timeout cleanup only sends kill() to the shell process PID, but steps are launched with create_subprocess_shell, so child/grandchild processes can outlive the timeout and keep running in the background. Start each command in its own process |
| ✅ Fixed | Suggestion | Inline | `src/dream/verification/_runner.py`:40 | Using or to select the verifier can silently replace a caller-provided verifier when that object is falsy, causing UI checks to be skipped unexpectedly. Use an explicit is None check so any provided verifier instance is always honored. [inc |

## PR #43 — feat(spec-07): wire task/cron/plan tools into default registry

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | High | Architect | `src/dream/tools/builtin/task_get.py`:54 | Unknown-id error guidance for task tools tells callers to "call task_list" to discover valid ids, but no task_list tool exists in the default registry or anywhere in the codebase, so the documented recovery path is impossible to follow. |
| ✅ Fixed | Possible-bug | Inline | `src/dream/tools/builtin/cron_show.py`:63 | Reading the cron registry is not wrapped in error handling, so filesystem errors (permission denied, path is a directory, transient IO issues) will raise and break the tool contract. Wrap get_cron_job(...) in a try/except OSError path and r |
| ✅ Fixed | Low | Inline | `src/dream/tools/builtin/task_output.py`:56 | This path relies on read_task_output, which currently reads the entire log file into memory before trimming to max_bytes; for large task logs this causes avoidable O(file_size) memory and latency spikes despite the tail-window interface. Us |
| ✅ Fixed | Low | Inline | `src/dream/tools/builtin/task_output.py`:61 | The structured retry guidance tells callers to use task_list, but that tool is not registered in the default registry. This creates an impossible recovery path for unknown task IDs and can trap the agent in repeated failing retries. Update  |
| ✅ Fixed | Low | Inline | `src/dream/tools/builtin/task_get.py`:53 | The retry guidance references task_list, but that tool is not registered in the default toolset, so automated recovery instructions point to a non-existent API path. Update the guidance to existing tools (for example task_create/known IDs)  |
| ✅ Fixed | Low | Inline | `src/dream/tools/builtin/task_stop.py`:54 | The stop-path error message tells callers to use task_list, but no such tool exists in the registered defaults, so the suggested remediation cannot be executed. Replace this with guidance that references available tools or known task IDs. [ |
| ➖ Already fixed (sys.executable; no cmd ref) | Suggestion | Inline | `tests/test_tools/test_builtin/test_task_create.py`:80 | This test hardcodes the Windows cmd executable for argv, so it fails on Linux/macOS where cmd does not exist and task_create returns a structured spawn error instead of creating a task. Use a platform-neutral executable (like sys.executable |
| ➖ Already fixed (sys.executable; no cmd ref) | Suggestion | Inline | `tests/test_tools/test_builtin/test_task_stop.py`:50 | The "running task" setup depends on cmd /c ping -n, which is Windows-specific and raises spawn failures on non-Windows CI, so the stop-path assertion never runs. Replace this with a cross-platform long-running command (for example via Pytho |
| ✅ Fixed | Suggestion | Inline | `src/dream/tools/builtin/task_create.py`:88 | The argument validation only checks for None, so an empty argv list slips through and reaches create_subprocess_exec(*argv), which raises a runtime TypeError (no program provided). Validate that argv is non-empty (or reject empty strings in |
| ✅ Fixed | Suggestion | Inline | `src/dream/tools/builtin/task_create.py`:124 | Spawn failures are only partially handled (ValueError/FileNotFoundError), but process creation can also raise other OSError subclasses (for example PermissionError or NotADirectoryError). Those exceptions currently escape as uncaught tool f |
| ✅ Fixed | Suggestion | Inline | `src/dream/repl/_session.py`:118 | This hardcodes Path.home() and bypasses DreamPaths.resolve(...), so DREAM_HOME overrides are ignored for task storage in REPL sessions. In environments that set DREAM_HOME, task artifacts will be written/read from the wrong root, causing cr |
| ✅ Fixed | Suggestion | Inline | `src/dream/tools/builtin/plan_show.py`:120 | Invalid task_id values (for example empty string or traversal-like ids) raise ValueError from read_plan/_checked_task_id, but _load only catches FileNotFoundError. That exception escapes the tool and bypasses the structured _err(...) contra |

## PR #44 — feat(repl): runtime/shell info in system prompt + inline background-task lifecycle; fix tracer per-anext driver

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | High | Architect | `src/dream/repl/_runtime_info.py`:30 | The runtime-info block reports POSIX shells using $SHELL (via detect_shell), but task_create command=... is actually executed with asyncio.create_subprocess_shell without an explicit executable, which always invokes /bin/sh on POSIX; this c |
| ✅ Fixed | Low | Inline | `src/dream/repl/_session.py`:891 | Listener unsubscription is only performed in the inner finally around start_session/session_loop, but listeners are registered before entering that block. If the function returns early (for example when MCP setup has blocking findings), tho |
| ✅ Fixed | Suggestion | Inline | `src/dream/repl/_runtime_info.py`:30 | POSIX shell detection is incorrect: asyncio.create_subprocess_shell(...) in your task runner does not consult $SHELL when no executable is provided, it uses /bin/sh. Advertising $SHELL in the system prompt can make the model emit shell-spec |

## PR #46 — Spec13/13a permission core

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/permissions/_checker.py`:52 | The tool allow-list currently short-circuits the pipeline to ALLOW, which bypasses command deny patterns, path deny rules, tier checks, and write-boundary enforcement. This lets any allow-listed tool run otherwise-blocked destructive comman |
| ✅ Fixed | Critical | Inline | `src/dream/permissions/_checker.py`:133 | Path deny matching uses only lexical path forms and does not evaluate a symlink-resolved form. A denied target can be accessed through an in-repo symlink path that does not match the deny glob lexically, causing policy bypass for path-denie |
| ✅ Fixed | Possible-bug | Inline | `tests/test_permissions/test_credential_guard.py`:81 | This test unconditionally creates a filesystem symlink, which raises OSError on environments where symlink creation is restricted (common on Windows CI or locked-down containers). That causes a platform-dependent test failure before the per |
| ✅ Fixed | Low | Inline | `tests/test_permissions/test_path_validator.py`:66 | This test also assumes symlink creation always succeeds, but symlink_to can fail with permission errors on some runners, producing a false-negative test failure unrelated to boundary validation logic. Add a symlink-support guard (or conditi |

## PR #47 — Spec13/13b policy config

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/engine/_permission_gate.py`:43 | This trusts tier_required for every tool in the registry, including discovered/MCP tools. That bypasses the trust-ramp behavior where newly discovered tools must default to read-only until explicitly promoted, so effectful external tools ca |
| ✅ Fixed | Critical | Architect | `src/dream/engine/_tool_dispatch.py`:121 | When a permission_gate is wired, the PermissionRequest is built solely from tool.effects_for(...), but several mutating built-in tools (e.g. task_create, task_stop, mcp_auth and MCP adapters) still use the default empty ToolEffects, so eval |
| ✅ Fixed | Critical | Inline | `src/dream/engine/_tool_dispatch.py`:121 | The permission request is built only from tool.effects_for(input), but many existing mutating tools (for example task-management tools) do not override effects_for and therefore produce an empty effect set. In the checker, non-read-only req |
| ✅ Fixed in engine _trusted_tiers (built-in/per-repo only) | Critical | Inline | `src/dream/permissions/_policy_builder.py`:44 | This merge trusts every entry in trusted_tiers as-is, and the current gate assembly populates that map from all registered tools (including discovered/MCP tools). That bypasses the intended trust-ramp behavior where discovered tools should  |
| ✅ Fixed | Suggestion | Inline | `src/dream/repl/_session.py`:211 | The returned policy warnings are explicitly discarded, so stale promotion and related policy warnings are never surfaced in the REPL despite being produced by policy assembly. This hides important operator-facing security signals; plumb the |

## PR #48 — Spec13/13d session limits

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Low | Inline | `src/dream/engine/_session.py`:465 | The session can break on a limit breach before running the checkpoint hook, even when the turn outcome is complete. That drops the latest successful snapshot and can cause duplicate work on resume. Move limit-enforcement after the successfu |

## PR #49 — Spec13/13e threat scan

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/services/threat_scan.py`:116 | A malformed .harness/lurkr-ignore.toml raises LurkrIgnoreError from load_lurkr_ignore, and this call path is not handled here, so session startup can crash with an exception instead of returning normal blocking findings. Catch that parse er |
| ✅ Fixed | Critical | Inline | `src/dream/services/threat_scan.py`:208 | Secret scanning is restricted to a small suffix allowlist, which lets secrets in other readable text files (for example .pem, .key, extensionless config files) bypass detection entirely. Expand file selection to cover general text files (or |
| ✅ Fixed | High | Architect | `src/dream/services/threat_scan.py`:97 | Path‑glob suppressions compare glob_to_regex patterns (which are POSIX "/"-based) against Finding.path strings built from Path.__str__, so on Windows (where these contain backslashes) valid ignore globs like "tests/" will not suppress findi |
| ✅ Fixed | Suggestion | Inline | `tests/test_repl/test_session_threat_gate.py`:52 | This assertion is too weak for the stated behavior: it passes for any non-3 exit code, including unexpected failures or accidental success. Assert the exact expected code (2) so the test actually verifies that clean repos pass the threat ga |
| ✅ Fixed | Suggestion | Inline | `tests/test_services/test_threat_scan.py`:67 | This test assumes POSIX permission semantics, but chmod(0o666) is not reliably represented on some platforms (notably Windows), which can make the assertion flaky or consistently fail cross-platform. Gate this test to POSIX or derive the ex |
| ✅ Fixed (warn-not-block) | Suggestion | Inline | `src/dream/repl/_session.py`:824 | This gate only runs threat_scan and never runs the structural repo checks, so sessions can start even when AGENTS.md/required docs/schema validations would have produced blocking findings. Use the combined session-start gate (or include val |
| ✅ Fixed | Suggestion | Inline | `src/dream/services/threat_scan.py`:127 | Suppression path matching is based on glob regexes that use POSIX-style /, but paths are built with str(...), which is backslash-separated on Windows. This causes valid ignore globs to fail to match and prevents intended suppressions on Win |

## PR #50 — Spec13/13f governance

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ➖ Already fixed (errors='replace') | Critical | Inline | `src/dream/services/core_beliefs.py`:39 | The file-read error handling only catches OSError, but read_text(encoding="utf-8") can also raise UnicodeDecodeError when the file contains non-UTF-8 bytes. That exception will currently bubble up and fail session startup, violating the int |

## PR #51 — feat(spec-07): wire cron triggers (scheduler + REPL tick + CLI)

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/repl/_cron_cli.py`:68 | There is a race between task completion and spawned_id assignment: the completion listener ignores events until spawned_id["id"] is set, but the spawned cron command can finish before that assignment. In that case done is never set and the  |
| ✅ Fixed | Critical | Inline | `src/dream/services/cron.py`:182 | The registry is marked as a successful run immediately after spawning, not after the task actually finishes. If the spawned session later fails, last_status and next_run are still recorded as success, which makes scheduler state incorrect a |
| ✅ Fixed | Critical | Inline | `src/dream/services/cron.py`:228 | The tick loop also marks each due job as successful right after spawn_cron_session returns, which only confirms spawn, not completion. This can record false success for jobs that later fail and prevents accurate retry/observability semantic |
| ✅ Fixed | High | Architect | `src/dream/services/cron.py`:182 | run_cron_kind bases enabled/disabled solely on the manifest's enabled flag and ignores the registry job's enabled state, so a job disabled via registry workflows (e.g. /cron toggle) will still be executed when fired via python -m dream.repl |
| ✅ Fixed | High | Architect | `src/dream/repl/_cron_cli.py`:60 | The cron CLI bootstraps manifest files but never seeds the registry from those manifests, so first-run or CLI-only cron setups can execute jobs while leaving the registry (and /cron list/cron_show state) empty and without last_run/last_stat |

## PR #52 — feat(spec-10/A): role manifest + capability minimisation

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Suggestion | Inline | `src/dream/config/paths.py`:168 | role is interpolated directly into a filesystem path without any segment validation, so values containing ../ or path separators can escape .harness/roles and point to arbitrary files. Constrain this argument to canonical role names (or app |

## PR #53 — feat(spec-10/BC): mailbox + handoff event + permission round-trip

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/swarm/_paths.py`:28 | IDs ending with a dot are currently accepted, which is unsafe on Windows because trailing dots are normalized away, so different IDs can resolve to the same directory. This can cause mailbox/permission path collisions and cross-leader state |
| ✅ Fixed | Critical | Inline | `src/dream/swarm/_paths.py`:53 | The validation uses re.match with a $-anchored pattern, which still accepts a trailing newline (for example "planner\n"). That allows whitespace IDs to bypass the "no whitespace" rule and creates unexpected on-disk directory names. Use full |

## PR #54 — feat(spec-10/D): team registry + teammate spawn + executors + depth cap

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/swarm/subprocess_backend.py`:131 | There is a race window where the completion listener can fire before captured_task_id["id"] is set, causing the listener to ignore the spawned task's terminal event and never write a task_notification. Set the tracked task id before events  |
| ✅ Fixed | High | Architect | `src/dream/swarm/subprocess_backend.py`:132 | SubprocessExecutor registers a completion listener for each spawn and only unregisters it on the exception path; on successful spawns the listener is never unregistered, so BackgroundTaskManager._listeners grows without bound and every late |
| ✅ Fixed | Low | Inline | `src/dream/swarm/_remote.py`:43 | agent_id is built from raw config.name/config.team instead of the new sanitizer contract, so values containing spaces or @ can produce inconsistent identities that won't match the sanitized IDs used by team registry/identity flows. Build th |
| ✅ Fixed | Low | Inline | `src/dream/swarm/_spawn.py`:71 | The same coercion issue exists for subscriptions: a string input is converted into character-by-character subscription tokens, producing invalid routing keys instead of a single topic value. Validate input type and reject strings before tup |
| ✅ Fixed | Suggestion | Inline | `src/dream/swarm/in_process.py`:114 | Completed/cancelled tasks are never removed from _tasks, so long-running leaders that spawn many teammates will retain finished task objects indefinitely. Remove entries when runner tasks finish (for example via a done callback or cleanup i |
| ✅ Fixed | Suggestion | Inline | `src/dream/swarm/_spawn.py`:69 | The tuple coercion treats any non-tuple iterable as valid, so passing a string for permissions silently turns it into per-character entries (for example "read" becomes ("r","e","a","d")), which breaks downstream permission semantics. Reject |

## PR #55 — feat(spec-10/E): planner role runs-once + spec/ledger + handoff event

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Suggestion | Inline | `src/dream/planner/_run.py`:37 | The runs-once guard has a TOCTOU race: two concurrent run_planner calls for the same task can both pass the exists() check before either write occurs, then both invoke the planner and overwrite artefacts. Add per-task synchronization (for e |

## PR #56 — feat(spec-10/F): sprint contract + negotiation + evaluator + outcome→…

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/sprint/_checks.py`:23 | checked_task_id does not reject : characters, which can produce invalid filenames on Windows and can map to NTFS alternate data streams (name:stream). This can cause lock/contract/eval path operations to fail or target unexpected file strea |
| ✅ Fixed | Critical | Inline | `src/dream/sprint/_evaluation.py`:114 | The write-once guarantee is vulnerable to a check-then-write race: two concurrent evaluators can both observe path.exists() as false and then both write, with the later os.replace overwriting the first record. Use an atomic create operation |
| ✅ Fixed | Critical | Inline | `src/dream/sprint/_outcome.py`:74 | This is a read-modify-write append pattern on a shared file, so concurrent calls can lose entries: each caller reads the same old content and the last write wins. Protect appends with a file lock or switch to an append-only write strategy t |
| ✅ Fixed | Low | Inline | `tests/test_sprint/test_negotiation.py`:66 | This assertion is too weak for the scenario: with accept_after(2) there should be exactly four negotiation entries (proposal+counter, proposal+accept), but >= 3 allows missing log entries to slip through. Tighten the assertion to the exact  |
| ✅ Fixed | Low | Inline | `src/dream/sprint/_negotiation.py`:88 | The function enforces only a lower bound for max_rounds, but spec behavior requires negotiation to be capped at 3 rounds; passing a value above 3 currently allows extra rounds and breaks that contract. Validate max_rounds with an upper boun |
| ✅ Fixed | Suggestion | Inline | `src/dream/sprint/_checks.py`:32 | checked_sprint_number only checks < 1 and never enforces that the value is a real integer, so values like 1.5 pass and generate non-spec filenames such as sprint-1.5.json. This breaks contract consistency with code that treats sprint number |
| ✅ Fixed | Suggestion | Inline | `src/dream/sprint/_contract.py`:121 | Parsing booleans with bool(...) will misread string values ("false" becomes True), so a malformed or externally-produced JSON contract can silently flip evaluator_enabled/imposed semantics. Parse these fields strictly as booleans (or raise  |
| ✅ Fixed | Suggestion | Inline | `src/dream/sprint/_outcome.py`:46 | The transition applies outcomes to any matching step regardless of its current status, so stale or misrouted evaluation records can incorrectly move pending, done, or blocked steps backward/sideways. Enforce that only the active in_progress |

## PR #57 — feat(spec-10/G): runner composition — run_task stitches planner + spr…

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/runner/_run.py`:180 | There is a race condition around step claiming: the next step is selected from ledger before taking the generator lock, then that stale step/ledger snapshot is used after lock acquisition. If two run_task calls overlap for the same task, bo |

## PR #59 — feat(spec-10): role-aware engine factory + negotiator heads + Harness.run_task facade (H)

| Status | Severity | Kind | File:Line | Finding |
|---|---|---|---|---|
| ✅ Fixed | Critical | Inline | `src/dream/engine/_permission_gate.py`:66 | This introduces a security contract mismatch: using the role toolset as tool_allow in the permission gate will *allow-list* those tools, not restrict to them. In the checker pipeline, tool_allow returns ALLOW early, which bypasses later pat |
| ✅ Fixed | Suggestion | Inline | `src/dream/sprint/_negotiation.py`:160 | In the sync negotiation path, when evaluator_propose returns an awaitable, the code raises TypeError but never closes/cancels that awaitable first. If the awaitable is a coroutine or task, this leaves it dangling and can trigger RuntimeWarn |
| ✅ Fixed | Suggestion | Inline | `src/dream/sprint/_negotiation.py`:176 | The same cleanup bug exists for generator_respond: when it returns an awaitable in the sync path, the function raises TypeError without closing/canceling the awaitable object. This can leak a live task or emit unawaited-coroutine warnings a |
| ✅ Fixed | Suggestion | Inline | `tests/test_runner/test_negotiator_heads.py`:331 | This assertion is too weak and can pass even if the round number is not actually included in the prompt, because the prompt already contains timestamps like 2025-... from the negotiation log. Assert a specific round token (for example the f |
