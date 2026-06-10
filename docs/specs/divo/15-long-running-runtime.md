# 15 — Long-Running Runtime (the harness becomes a daemon)

> Status: PLAN (2026-06-10). Synthesizes the state of all 27 `src/dream/*` packages,
> the spec 00–14 series, the left-over specs, the live findings from the SWE-bench
> eval work, and the `/tmp/OpenHarness` reference taxonomy.

## 0. North star

Today dream is **request-scoped**: every capability is driven by a foreground
process — the interactive REPL or a one-shot `run_task()`. The daemon-shaped
machinery already exists *in pieces* (cron tick loop, wake-cycle heartbeat,
BackgroundTaskManager, checkpoints/resume, session limits, swarm workers), but
the only thing that composes them into a living process is
`run_session_repl()` — a UI function.

**Definition of done for "long-running construct":**

```python
import dream

harness = dream.build_harness(model=..., api_key=..., base_url=..., working_dir=...)
async with dream.Runtime(harness) as rt:
    await rt.run_forever()        # days, not minutes
```

or headless: `python -m dream.daemon --working-dir <repo>`. While running it must:

1. **Fire triggers** — cron jobs and wake-cycle heartbeats fire with no REPL open.
2. **Execute work** — tasks (`run_task` sprints, background shell tasks, swarm
   workers) run under supervision with budgets and liveness watchdogs.
3. **Survive restarts** — on boot, resume in-flight tasks from sidecar/checkpoints;
   on shutdown, drain and checkpoint. Single-instance file lock.
4. **Be steerable from outside** — submit/cancel/status via a channel, not stdin.
5. **Be observable** — one stable JSONL event stream + queryable status, with
   `status / summary / next_actions / artifacts` on every surface.

Everything below serves those five sentences.

## 1. Component map — what exists, where it lives, what state it's in

### Built and solid (the substrate)

| Package | Role | Long-running relevance |
|---|---|---|
| `engine/` | turn loop, session FSM, liveness heartbeat, orientation, reviewer, auto-compaction | the inner loop; already has coma detection + typed turn outcomes |
| `runner/` | planner/generator/evaluator heads, `run_task`, observer | the work unit; heads auto-wire from harness |
| `tasks/` | BackgroundTaskManager, cron manifests/registry, ledger, FSM | already daemon-shaped; lifecycle listeners exist |
| `services/cron.py` | croniter scheduler + `cron_tick_loop` | **exists but only started by the REPL** (`repl/_session.py`) |
| `wake/` | wake-cycle heartbeat (Spec 06.5): WakeSource, HeartbeatDecision, per-agent lock, `run_wake_cycle` | the "should I start work?" brain between sessions — **no scheduler drives it** |
| `swarm/` | worktrees, team registry, file mailbox, permission round-trip, in-process + subprocess backends | multi-agent substrate; `_remote.py` is a gated stub |
| `coordination/` | claim/lease board (Spec 08) | stuck-task takeover primitive — built, not wired to a watchdog |
| `state/` | sidecar state.json, git-ref checkpoints | resume substrate — **nothing calls it at boot** |
| `permissions/` | tiers, policy, checker, SessionLimiter | governance for unattended operation ✅ |
| `verification/`, `observability/`, `sprint/`, `planner/`, `roles/`, `skills/`, `mcp/`, `api/`, `config/`, `contracts/`, `tools/`, `utils/` | as specced | done; consumed via the layers above |

### REPL-trapped (built, but composition lives in `repl/_session.py`)

- **Harness construction** — `build_default_harness()` (engine factory, tool
  registry, tasks+cron bootstrap, skills, system prompt, policy warnings) is in
  `dream/repl/_session.py` and reads `DREAM_SMOKE_*`. The public `HarnessConfig`
  only accepts an underscore-private `_engine_factory`; `config.extra` smuggles
  `task_manager` and `cron_registry_path`. **A real consumer cannot construct a
  runnable harness from `import dream`.** (Proven by the SWE-bench eval falling
  back to the private import.)
- **Cron tick loop** — `cron_service.cron_tick_loop` is spawned inside
  `run_session_repl` and dies with the REPL.
- **Task lifecycle rendering/listeners** — registered by the REPL.
- **System prompt assembly** — `_assemble_system_prompt` in the REPL module
  (the `prompts/` package is a docstring stub).
- **Session-start gates** (skills/threat/structural validators) — sequenced by
  the REPL, not by a reusable runtime boot.

### Stubs (docstring-only)

`sandbox/`, `plugins/`, `prompts/`, `memory/`, `hooks/` — i.e. exactly the
spec-13/14 surface plus the memory substrate from spec 11.

## 2. OpenHarness taxonomy → dream mapping

`/tmp/OpenHarness` is a file-less skeleton, but its directory tree is a
reference architecture for a *product-grade long-running harness*. Mapping:

| OpenHarness | dream today | Verdict |
|---|---|---|
| `engine/` | `engine/` | ✅ have |
| `coordinator/` | `coordination/` + `swarm/` + `runner/` | pieces exist; **no supervisor that owns them** |
| `channels/` (bus + impl) | `swarm/_mailbox.py` (worker-scoped only) | **gap** — no runtime-level inbound channel |
| `api/` + `ohmo/gateway` | — (dream `api/` = provider substrates, different thing) | **gap** — no serving/control surface |
| `bridge/` | `swarm/_remote.py` always-refuse stub | deferred seam (correct) |
| `autopilot/` + `services/autodream` | — | **deliberately not harness** — autopilot (spec 09) and dream-phase curation (spec 11) are *employees* in the business repo (Model A decision). dream ships the substrate they ride on. |
| `memory/` + `services/memory_extract` + `services/session_memory` | `memory/` stub + `services/context_log` + `services/core_beliefs` | substrate gap (read-side store, session memory) |
| `hooks/`, `plugins/`, `sandbox/`, `prompts/` | stubs | spec 13/14 remainder |
| `services/compact` | `services/compact/` | ✅ have |
| `permissions/`, `mcp/`, `config/` | same names | ✅ have |
| `auth/`, `services/oauth` | `mcp/_credentials` covers MCP; provider keys via env | sufficient for SDK; revisit for gateway |
| `frontend/terminal` (a *separate client* of the core) | `repl/` *inside* the package, owning composition | **the inversion to fix** — REPL must become a client of the runtime |
| `commands/`, `keybindings/`, `output_styles/`, `personalization/` | REPL slash commands | CLI-product concerns; not SDK scope now |
| `autopilot-dashboard` | `python -m dream.repl watch` | events JSONL already supports this |

The structural lesson from OpenHarness's root: **the terminal is a frontend,
not the spine.** dream has the spine's vertebrae built; they're just threaded
through the frontend.

## 3. The plan — five phases

Dependency spine: **P0 → P1** are sequential; **P2 and P3** parallelize after P1;
**P4** items are on-demand after P1; **P5** last. Each phase is spec-sized,
TDD'd, gated by ruff + mypy(strict) + full pytest, landed in small PRs.

### Phase 0 — Public construct-and-run API (the approved "full lift")

*The foundation; already user-approved.*

1. New `dream/_factory.py` (public): `build_harness(*, model, api_key,
   base_url="https://api.openai.com/v1", working_dir, max_turns=8,
   registry=None, skill_registry=None, ...) -> Harness` — the engine/tools/
   tasks/cron/skills wiring lifted out of `repl/_session.py`, with explicit
   params instead of the `DREAM_SMOKE_*` env contract.
2. `HarnessConfig` gains typed fields (`task_manager`, `cron_registry_path`,
   ...) — retire the `config.extra` smuggling.
3. `repl/_session.build_default_harness` becomes a thin wrapper: read env →
   call `dream.build_harness`. Re-export from `dream/__init__.py`.
4. First consumers: `examples/run_task_demo.py`, the SWE-bench eval runner.

**Exit test:** `python -c "import dream; dream.build_harness(...)"` runs a task
with zero imports from `dream.repl`.

### Phase 1 — `dream/runtime/`: the long-running construct itself

The new package that owns what the REPL currently owns. Hybrid architecture
per the harness-construction model: deterministic supervisor loops outside,
ReAct/LLM only inside turns.

1. **`Runtime` class** (`dream/runtime/_runtime.py`):
   - owns: BackgroundTaskManager, cron tick loop, wake scheduler, lifecycle
     listeners, event sink.
   - `async with Runtime(harness, config) as rt: await rt.run_forever()`.
   - boot sequence = today's REPL gate order: structural validate (warn) →
     threat scan (block) → skills gate → **resume pass** (scan sidecars/
     checkpoints, re-queue or adopt in-flight tasks) → start loops.
   - shutdown: stop accepting, drain or checkpoint running tasks, final
     sidecar write, release locks.
   - single-instance: `utils/file_lock` on `.dream/runtime.lock`.
2. **Wake scheduler** (`dream/runtime/_wake_scheduler.py`): drives
   `wake.run_wake_cycle` from its WakeSources (idle timer, cron-wake) on a
   loop — today nothing fires wake except the REPL `/wake` command. Decisions
   that say "start work" enqueue tasks via the task manager.
3. **Supervision policy**: every loop (cron tick, wake, watchers) gets the
   crash-isolation the cron loop already has (log-as-event, continue), plus
   restart counters surfaced as `runtime.health` events.
4. **`python -m dream.daemon`** entrypoint: env/config → `build_harness` →
   `Runtime.run_forever()` with signal handling (SIGTERM = graceful drain).
5. **REPL inversion**: `run_session_repl` constructs a `Runtime` and talks to
   it; deletes its private cron task, lifecycle listener registration, and gate
   sequencing. The REPL is now one frontend (OpenHarness `frontend/terminal`).

**Exit test:** start daemon in a repo with a 1-minute cron manifest and an idle
wake source; kill -9 it mid-task; restart; observe: cron fired with no REPL,
wake decision recorded, in-flight task resumed/adopted, one JSONL stream tells
the whole story.

### Phase 2 — Control plane: channels in, events out

*The OpenHarness `channels/` + `api/` analog, file-first like everything else.*

1. **Inbound channel** (`dream/channels/`): runtime-level inbox — a drop-dir of
   JSON command files (atomic-write, same pattern as `swarm/_mailbox`):
   `submit_task{intent, budget}`, `cancel{task_id}`, `status{}`, `wake{}`.
   The runtime polls/watches and acks each command with a result event.
   (Unix-socket/HTTP gateway is a later adapter behind the same command types —
   the `ohmo/gateway` seam.)
2. **Outbound stream**: standardize on the existing events JSONL as *the*
   API: documented event-type catalogue, stable location
   (`.dream/runtime/events.jsonl`), rotation. `python -m dream.repl watch`
   already tails it; add `dream.tail()` helper for SDK consumers.
3. **Observation contract** (skill rule): every command ack and status reply
   carries `status / summary / next_actions / artifacts` — same shape as
   ToolResult metadata, so agents and humans read one grammar.
4. **CLI**: `python -m dream.ctl submit|status|cancel|wake` writing to the
   inbox — chorus's employees and humans use the same door.

**Exit test:** with the daemon running, `dream.ctl submit "fix X"` from another
process starts a sprint; `dream.ctl status` shows it; cancel works; everything
visible in the event stream.

### Phase 3 — Deterministic verification + recovery hardening (10p5 + 12)

*Turns "runs for days" into "runs for days and the work is real."*

1. **Oracle execution** (top finding from the SWE-bench eval): the runner
   executes the contract's `verification_steps` itself (subprocess, sandbox
   tier-gated) and hands the structured results to the evaluator head — the
   evaluator judges *evidence*, not vibes. `pass` requires the oracle green
   when verification steps exist.
2. **Liveness watchdog** (spec 10p5): runtime-level monitor that walks the
   claim/lease board (`coordination/`) — expired lease ⇒ mark stale, emit
   event, apply policy (requeue | takeover | abandon). Wire wake's anti-coma
   forced mode in as the detector for silent stalls.
3. **Budgets**: per-task caps (wall-clock, tokens/$, sprints) enforced by the
   runtime around `run_task`; SessionLimiter defaults tuned for long-horizon
   work (current defaults were sized for REPL sessions).
4. **Retry policy**: failed tasks retry per the 3-part error contract
   (root_cause/safe_retry/stop_condition) already emitted by tools — the
   runtime finally *reads* stop_condition instead of only surfacing it.

**Exit test:** dream-eval (SWE-bench-verified-100 pure-python subset) runs
under the daemon via `dream.ctl submit`, with oracle-graded verdicts; a
deliberately wedged task is detected and requeued by the watchdog.

### Phase 4 — Fill the stubs the long-running construct actually needs

On-demand after P1, in this order of leverage:

1. **`prompts/`** — lift `_assemble_system_prompt` out of the REPL (pure
   function: catalogue + runtime_info + standing orders → prompt). Needed by
   P0 anyway; cheapest first.
2. **`hooks/`** — PreToolUse/PostToolUse/Stop bus (contracts/hook.py exists).
   Unattended operation needs interception points for governance; the
   permission gate is the precedent.
3. **`plugins/`** — loader for tool/hook/provider bundles; **this is how
   chorus/lattice/horizon inject their stuff** without forking the SDK.
   `Harness.register_plugin` exists; add discovery + manifest validation.
4. **`memory/`** — substrate only (read-side store + session-memory extract à
   la OpenHarness `services/memory_extract`/`session_memory`): persist per-repo
   memory records, expose read tools. **Curation/evolution stays an employee**
   (spec 11 brain lives in the business repo — Model A).
5. **`sandbox/`** — formalize subprocess as the default adapter; optional
   docker adapter (needed anyway for the compiled-deps SWE-bench tier and
   Terminal-Bench).

### Phase 5 — Multi-agent under supervision + remote seam

1. Swarm subprocess workers become runtime-supervised children (restart
   policy, mailbox + permission round-trip already built).
2. The runtime's channel becomes the leader↔worker boundary for cross-process
   teams; team registry (`swarm/_registry.py`) records what the runtime hosts.
3. `bridge/` (OpenHarness analog of `swarm/_remote.py`): keep refusing until a
   real remote backend exists; the seam is already shaped.
4. **Explicit non-goal:** autopilot pipeline (spec 09) and dream-phase curation
   (spec 11 brain) are built in the business repo as employees that *use*
   wake + channels + memory substrate. dream stays business-logic-free.

## 4. Cross-cutting quality bars (harness-construction lens)

- **Action space:** runtime commands are micro-tools (submit/cancel/status/
  wake) with strict schemas — no catch-all "do(...)" command. Tool registry
  stays the single action-space source.
- **Observation:** one event grammar everywhere; every reply has
  `status/summary/next_actions/artifacts`. No opaque outputs.
- **Recovery:** every loop crash-isolates and reports; every error path keeps
  the 3-part contract; the watchdog acts on leases, not heuristics.
- **Context budget:** compaction stays at phase boundaries (auto-compact +
  core-beliefs digest survival already in); wake decisions stay single-turn;
  daemon never accretes context — state lives in files, not transcripts.
- **Benchmarks (regression suite for the runtime):** dream-eval +
  swe-bench-verified-100 tracked on completion rate, retries/task, pass@1,
  cost per resolved task. Terminal-Bench tier later via the sandbox adapter.

## 5. Sequencing summary

| Phase | Size | Depends on | Headline |
|---|---|---|---|
| P0 public API | S (mostly moves) | — | `dream.build_harness()` |
| P1 runtime | M | P0 | `Runtime.run_forever()` + daemon + REPL inversion |
| P2 channels | M | P1 | steer it from outside |
| P3 oracle+recovery | M | P1 (P2 helps) | work is verified; stalls are caught |
| P4 stubs | S each | P1 | prompts → hooks → plugins → memory → sandbox |
| P5 swarm/remote | M | P2+P3 | supervised teams; remote seam stays gated |

Left-over specs folded in: 05 (structural validator wiring) lands in P1 boot;
02 (rolling pass-rate) lands in P3 benchmarks; 01/03/04 unchanged.
