# digest — a rolling AI-news digest agent on the dream runtime

The **clock-driven** example agent (companion to wake-driven [`ohmo`](../ohmo/README.md)).
A dream **cron** manifest fires every 2 hours — starting the moment you launch —
and each run drops a timestamped markdown file under `research_ideas/` covering
the last 2 hours of self-evolution AI news (Hacker News + arXiv). No email — the
repo is the system of record.

## What's the agent vs. what's the runtime

| capability | where it comes from |
|---|---|
| fire every 2 hours, survive restarts, single-instance lock | dream `cron` + `Runtime` |
| spawn each run as a supervised, drained background task | runtime cron loop + `cron_argv_builder` |
| "starting from now" first fire | `fire_now()` backdates the seeded `next_run` |
| steer / observe it | `python -m dream.ctl … status\|events` + `.dream/runtime/events.jsonl` |
| the news sources, the window, the file format | this agent (`tools.py`, `persona.py`) |

The agent adds two tools (`hn_search` over an hour window, `save_digest` →
`research_ideas/{timestamp}.md`), reuses ohmo's pinned-host `arxiv_search`, and
a persona tuned for short, honest, window-scoped digests.

## Run it (every 2 hours, from now)

```bash
export DREAM_API_KEY=sk-...        # any OpenAI-compatible endpoint
export DREAM_MODEL=gpt-4.1
./examples/digest/run.sh ~/digest-lab
# or directly:
python examples/digest/agent.py --workspace ~/digest-lab
```

On boot it lays down `research_ideas/`, `.harness/sandbox.toml` (the
`repo-write+net-allowlist` tier so the search tools run), trust-ramp promotions
for its three tools, and the `rolling-digest` cron manifest (`0 */2 * * *`). The
first run is backdated to *now*, so a file appears within ~30s; subsequent runs
land on the even-hour boundaries.

## One now

```bash
./examples/digest/run.sh --once ~/digest-lab
# writes research_ideas/{timestamp}.md and exits
```

This `--once` mode is exactly what the cron loop spawns each cycle.

## Steer / watch it

```bash
python -m dream.ctl --working-dir ~/digest-lab status
python -m dream.ctl --working-dir ~/digest-lab events --last 20   # runtime.task.* per firing
ls ~/digest-lab/research_ideas/                                   # the digests, sortable by name
```

Customize: `--topic "your topic"`, `--window-hours 6`. Stop with Ctrl-C/SIGTERM —
an in-flight digest drains, the lock releases.

## Tests

`uv run pytest tests/test_examples/test_digest.py` — HN parsing + hour window,
file delivery, bootstrap (manifest + promotions), the fire-now backdating, and
the cron argv payload, all offline.
