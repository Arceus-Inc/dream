"""digest — a rolling AI-news digest agent on the dream runtime.

The second example agent *shape*: where ohmo is wake-driven (an idle
heartbeat decides whether to work), digest is **clock-driven** — a dream
cron manifest fires every 2 hours (starting from now), the runtime spawns
a one-shot digest run as a supervised background task, and the result
lands in ``research_ideas/{timestamp}.md``. No email — the repo is the
system of record.

Default mission: AI news about **self-evolution** — self-improving and
self-evolving agents/models — gathered from Hacker News and arXiv over
the last 2 hours.

Run it (every 2 hours, starting now)::

    DREAM_API_KEY=... DREAM_MODEL=... python examples/digest/agent.py \
        --workspace ~/digest-lab

One-shot (what cron spawns; also "give me one now")::

    python examples/digest/agent.py --once --workspace ~/digest-lab
"""
