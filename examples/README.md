# Building long-running agents with `dream`

`dream` is an SDK, not an app: it ships the *mechanism* of a long-running
agent — the session loop, tools, scheduling, supervision, governance, and
verification — and you ship the *policy*: a persona, a few tools, and a
trigger. This guide shows how the three example agents in this folder are
built, so you can build your own the same way.

## The three examples (pick the trigger shape that matches your agent)

| example | trigger | what it does | read it for |
|---|---|---|---|
| [`ohmo/`](ohmo/README.md) | **wake heartbeat** — an idle timer fires a one-turn "should I work?" decision | always-on research assistant: finds papers, writes briefs, keeps a reading queue | self-directed agents that decide their own work |
| [`digest/`](digest/README.md) | **cron** — fixed schedule, every run is fresh | rolling AI-news digest: every 2h, a timestamped file covering the window | recurring report/monitor jobs |
| [`research_claw/`](research_claw/README.md) | **cron + queue** — schedule pops work items | paper factory: ideas in `ideas.md`, experimentally tested papers out | scheduled workers with an audited deliverable |

All three run on the same five-line skeleton.

## The skeleton

```python
import asyncio
from pathlib import Path
from dream import Runtime, RuntimeConfig, build_harness

async def main() -> None:
    harness = build_harness(
        model="gpt-4.1",                  # any OpenAI-compatible endpoint
        api_key="sk-...",                 # never read from env by the SDK itself
        base_url="https://api.openai.com/v1",
        working_dir=Path("~/my-agent-lab").expanduser(),
    )
    async with Runtime(harness, RuntimeConfig(agent_id="my-agent")) as rt:
        await rt.run_forever()            # days, not minutes

asyncio.run(main())
```

That alone gives you: a **single-instance lock**, **boot gates** (malformed
skills and worktree secrets block boot), a **resume scan** of work a dead
process left behind, a **command channel** (steer it from another terminal
with `python -m dream.ctl`), a **liveness watchdog**, supervised loops with
crash isolation, **graceful SIGTERM drain**, and one observable event stream
at `<workspace>/.dream/runtime/events.jsonl` (catalogue:
[`docs/runtime-events.md`](../docs/runtime-events.md)).

What it does *not* give you is a reason to wake up and tools to act with.
That's your agent. Four steps:

## 1. Give it tools (the action space)

Subclass `BaseTool`: a name, a model-facing description, a pydantic input
schema, and a declaration of risk + sandbox tier. Keep tools *micro* — strict
schemas, pinned hosts, no catch-all "do(...)" door.

```python
from pydantic import BaseModel, Field
from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

class LookupInput(BaseModel):
    query: str = Field(min_length=2, max_length=200)

class LookupTool(BaseTool):
    name = "lookup"
    description = "Search <your pinned source>. The model controls query terms only."
    declaration = ToolDeclaration(risk="external", tier_required=2, timeout_seconds=30.0)
    input_model = LookupInput

    async def execute(self, input, ctx: ToolExecutionContext) -> ToolResult:
        params = LookupInput.model_validate(input)
        try:
            body = await self._fetch(params.query)        # your HTTP call
        except Exception as exc:
            return ToolResult(
                content=f"lookup failed: {exc}",
                is_error=True,
                metadata={                                  # the 3-part recovery contract
                    "root_cause": f"request failed: {exc}",
                    "safe_retry": "retry once, then continue without it",
                    "stop_condition": "stop after two consecutive failures",
                },
            )
        return ToolResult(content=body, metadata={"summary": "1 result"})
```

Register yours on top of the builtins (file read/write/edit, bash, git,
background tasks, skills…):

```python
from dream.tools._registry import ToolSource
from dream.tools.builtin import default_registry

registry = default_registry()
registry.register(LookupTool(), source=ToolSource.PER_REPO)
harness = build_harness(..., registry=registry)
```

Conventions that pay off (see `ohmo/tools.py`, `research_claw/tools.py`):
**pin hosts** (the model chooses query terms, never URLs), **archive before
delivering** (a flaky network must never lose the work), **validate paths
against the workspace boundary**, and always fill the
`root_cause / safe_retry / stop_condition` metadata on errors — the engine
turns it into recovery guidance for the model.

## 2. Give it a persona (the policy)

A persona is a system prompt that states identity, workspace conventions
(*which files are the durable state*), the working doctrine, and hard rules.
The single most important line in a long-running persona: **durable state
lives in files, never in your head** — the process outlives any one session.

```python
from dream.session import SessionOptions

session = await harness.start_session(
    SessionOptions(system_prompt=MY_PERSONA, max_turns=16)
)
async for event in session.send("the task for this session"):
    ...   # typed events: TextDelta, ToolUseStart/Result, TurnComplete, Error
```

See `ohmo/persona.py` (an agent with a mission and file conventions) and
`research_claw/personas.py` (an agent that owns a whole craft in one session).

## 3. Give it a reason to wake up (the trigger)

**Wake heartbeat** — for self-directed agents. The runtime periodically runs a
*single decision turn*: run or skip. Skips are free; an anti-coma guard forces
a wake after too many consecutive skips. Your handler turns a `run` decision
into real sessions:

```python
rt = Runtime(
    harness,
    RuntimeConfig(
        agent_id="ohmo",
        wake_idle_minutes=30,
        wake_prompt_path=heartbeat_md,   # your persona's "should I work?" prompt
    ),
    wake_run_handler=my_handler,         # async (HeartbeatDecision) -> None
)
```

Tip from `ohmo/agent.py`: the heartbeat prompt file is re-read every cycle, so
*rewrite it with live workspace state* (queue length, work done) after each
session — that's how a one-turn heartbeat "sees" without tools.

**Cron** — for clock-driven work. Drop a manifest in
`<workspace>/.harness/cron/my-job.toml`:

```toml
name = "my-job"
enabled = true
schedule = "0 */6 * * *"
```

and tell the runtime what a firing should execute (typically your own script's
one-shot mode, spawned as a supervised background task):

```python
rt = Runtime(harness, config, cron_argv_builder=lambda m: [sys.executable, "agent.py", "--once"])
```

To start *now* instead of at the next boundary, backdate the seeded `next_run`
(see `fire_now()` in `digest/agent.py`). Schedules survive restarts.

**The command channel** — always on, no wiring needed. From any other process:

```bash
python -m dream.ctl --working-dir ~/my-agent-lab status
python -m dream.ctl --working-dir ~/my-agent-lab submit "do this task"
python -m dream.ctl --working-dir ~/my-agent-lab wake
python -m dream.ctl --working-dir ~/my-agent-lab events --last 20
```

These compose: ohmo uses wake + channel; research_claw uses cron + a file
queue; nothing stops you using all three.

## 4. Verify the deliverable (the oracle habit)

Never let "the model said it worked" be the success signal. After the agent's
session, check the deliverable deterministically — and when the deliverable
*is* code, **run it yourself**:

```python
from dream.sandbox import SubprocessSandbox   # tree-killed timeouts, explicit env
result = await SubprocessSandbox().run("python experiment.py", cwd=workspace, timeout_seconds=120)
verified = result.returncode == 0
```

`research_claw` is the full pattern: the agent iterates `run_experiment` until
green *in-session*, then the harness re-runs the script *itself* afterwards and
stamps `VERIFIED / UNVERIFIED / NO-PAPER` into an index. Tell the persona the
audit will happen — and that a result it couldn't verify must be disclosed, not
invented.

## Governance: two files in every workspace

Your bootstrap should write these once (all three examples do):

```toml
# .harness/sandbox.toml — the session's capability tier
tier = "repo-write+net-allowlist"     # tier-2 tools (network) need this

# .harness/tool-tier-overrides.toml — the trust ramp
[lookup]
tier_required = "repo-write+net-allowlist"
promoted_by = "my-agent-bootstrap"
promoted_at = "2026-06-10"
reason = "pinned-host search; query terms only"
```

**The gotcha that will bite you first:** per-repo tools start **read-only**
regardless of what they declare — your bootstrap (the workspace operator) must
promote each one, or every call is denied. We watched an agent respond to that
denial by trying to *edit the overrides file itself*; the credential-path guard
now blocks writes to all `.harness/` policy files, so promotion is genuinely
operator-only. Don't skip the promotions.

## Debugging a live agent

- `python -m dream.ctl ... status` — loops, jobs, running tasks.
- `.dream/runtime/events.jsonl` — everything that happened, one JSONL line
  each ([catalogue](../docs/runtime-events.md)); `dream.tail_events(path)` in code.
- `.dream/sidecars/<session>/logs/trace.jsonl` — per-session traces: every LLM
  call, tool call, and `tool.is_error` flag. When an agent "did nothing", this
  is where you find the tool denial that explains it.
- A second daemon in the same workspace exits with "already running" — the
  runtime lock is per-workspace, by design.

## Checklist for a new agent

1. Workspace bootstrap: dirs + `sandbox.toml` + tool promotions (+ cron
   manifest / heartbeat prompt), all idempotent.
2. Tools: micro, pinned, boundary-checked, 3-part error contract.
3. Persona: identity, file conventions, hard rules, honesty.
4. Trigger: wake / cron / channel / one-shot — or a combination.
5. Verification: a deterministic check (or sandbox re-run) of the deliverable.
6. A `--once` mode: makes the agent testable, cron-spawnable, and demoable.
7. Tests with injected fakes (every example has an offline test suite under
   `tests/test_examples/` — fetchers, session runners, and clocks are all
   injectable seams).

Then: `run.sh`, drop work in the queue, and let it run for days.
