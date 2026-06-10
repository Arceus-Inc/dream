# ohmo — an always-on research agent on the dream runtime

The OpenHarness taxonomy reserves `ohmo/` for the persona'd, long-lived
assistant that rides on the harness core. This is dream's equivalent, built
entirely from the **public SDK surface** — it is also the reference example of
the Model A shape: the SDK ships the mechanism, the agent (persona, tools,
workspace conventions) is policy that lives outside `src/dream`.

## What it is

A research agent that runs for days. It finds papers (arXiv), reads them, and
prepares research content about them: five-minute briefs under
`docs/research/briefs/`, indexed in `docs/research/INDEX.md`, with a durable
reading queue in `docs/research/queue.json` so work survives across sessions
and restarts.

Everything "long-running" comes from `dream.Runtime`:

| capability | where it comes from |
|---|---|
| single-instance lock, boot gates (skills/threat scan), resume scan | `Runtime.start()` |
| self-directed work between sessions | wake-cycle heartbeat (`--wake-idle-minutes`), with ohmo's own heartbeat persona (`.harness/ohmo-heartbeat.md`) |
| steerable from outside | command inbox — `python -m dream.ctl submit/cancel/status/wake` |
| scheduled jobs | cron manifests under `.harness/cron/` |
| observability | one events JSONL (`.dream/runtime/events.jsonl`, see `docs/runtime-events.md`) plus `ohmo.research.*` events |
| crash discipline | supervised loops, lease watchdog, graceful drain on SIGTERM |

The agent itself adds three micro-tools (`arxiv_search`,
`save_research_brief`, `reading_queue`), two prompts (the session persona and
the heartbeat persona), and a workspace bootstrap.

## Run it

```bash
export DREAM_API_KEY=sk-...        # any OpenAI-compatible endpoint
export DREAM_MODEL=gpt-4.1         # DREAM_BASE_URL to point elsewhere
python examples/ohmo/agent.py --workspace ~/ohmo-lab --wake-idle-minutes 30
```

On first start ohmo lays down its conventions in the workspace:
`docs/research/` (briefs + index), `.harness/sandbox.toml` (the
`repo-write+net-allowlist` tier so the tier-2 `arxiv_search` tool is
callable), and `.harness/ohmo-heartbeat.md` (edit it to retune the wake
persona).

Every `--wake-idle-minutes`, the heartbeat fires one decision turn: *should
ohmo start work, and on what?* A `run` decision spawns one persona session per
task; a `skip` is recorded and the streak feeds the anti-coma guard (a forced
wake after too many consecutive skips).

## Steer it from another terminal

```bash
python -m dream.ctl --working-dir ~/ohmo-lab status
python -m dream.ctl --working-dir ~/ohmo-lab wake                 # heartbeat now
python -m dream.ctl --working-dir ~/ohmo-lab submit "research mamba-2 ssm duality"
python -m dream.ctl --working-dir ~/ohmo-lab events --last 20     # tail the stream
```

`submit` routes through the sprint pipeline (planner → generator → evaluator
with oracle-verified contracts); the wake path drives direct persona sessions.
Stop with SIGTERM/Ctrl-C — running work drains, the lock releases, and the
reading queue carries unfinished work into the next run.

## Tests

`uv run pytest tests/test_examples/test_ohmo.py` — feed parsing, tool file
mechanics, bootstrap idempotence, exit codes, and the wake handler, all
offline.
