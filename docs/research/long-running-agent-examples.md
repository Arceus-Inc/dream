# Long-Running Agent Harnesses: Ideas & Example Agents for dream

*Generated: 2026-06-10 | Sources: 21 | Confidence: High on patterns, Medium on usage-stat claims (single-source)*

## Executive Summary

The long-running agent ecosystem converged in 2025–2026 on a small set of recurring
mechanisms — heartbeat wakes over a checklist file, cron with isolated-vs-persistent
session modes, file/git-based progress that outlives any context window, a gateway
process owning channels, and separated generate/evaluate roles. dream already
implements most of these (spec 15: Runtime, wake, cron, channels, oracle-verified
evaluator), so the highest-leverage next step is not more machinery but **a catalogue
of example agents** that exercise the machinery — the way OpenFang ships seven
"hands" and OpenClaw's adoption is driven by use-case galleries. This report maps the
landscape, extracts the patterns dream still lacks, and proposes 12 example agents
ranked by which runtime capabilities they exercise.

## 1. The landscape: six archetypes of "long-running"

**Gateway personal assistant (OpenClaw/Moltbot).** A single long-lived Gateway
process is "the single source of truth for sessions, routing, and channel
connections" (WebSocket bind, systemd/LaunchAgent), with Telegram/WhatsApp/Slack/
Discord channels and a model-agnostic core ([innFactory architecture explainer](https://innfactory.ai/en/blog/openclaw-architecture-explained/),
[openclaw.ai](https://openclaw.ai/)). Proactivity comes from two distinct
mechanisms: a **heartbeat** (default every 30 min) that reads `HEARTBEAT.md` — a
checklist of things to proactively check — and replies `HEARTBEAT_OK` when nothing
needs attention (suppressed, never delivered), vs **cron** for explicit scheduled
payloads ([OpenClaw cron docs](https://docs.openclaw.ai/automation/cron-jobs),
[heartbeat guide](https://open-claw.bot/guide/heartbeat-automation)). Cron jobs
support `at`/`every`/cron-expression schedules, run *isolated* fresh sessions,
*main-session* event injection, or *custom sessions persisting context across runs*,
with `announce`/`webhook` delivery — all stored in SQLite so schedules survive
restarts ([cron docs](https://docs.openclaw.ai/automation/cron-jobs)).

**Cloud-native autonomous agent (Hermes).** Nous Research's Hermes Agent (Feb 2026)
is "designed for long-term autonomous cloud deployment": agent-curated memory with
periodic nudges, autonomous skill creation after complex tasks, FTS5 session search
with LLM summarization for cross-session recall, natural-language cron, and
serverless persistence (hibernates idle, wakes on demand via Daytona/Modal)
([hermes-agent repo](https://github.com/nousresearch/hermes-agent),
[hermes-agent.org](https://hermes-agent.org/)).

**Fork-and-run agent template (OpenFang).** A Rust "agent OS" whose pitch is the
bundle of ready agents — seven "hands": video clipper, lead prospector with ICP
scoring, OSINT collector, superforecaster with confidence intervals, researcher with
CRAAP source grading, Twitter manager, and purchase-gated browser automation — each
a manifest (tools, settings, dashboard metrics) + a 500-word operational playbook +
SKILL.md ([openfang repo](https://github.com/RightNow-AI/openfang)). The lesson:
**the example agents are the product surface**.

**Repo-resident maintenance agents (GitHub Agentic Workflows).** Coding agents run
in GitHub Actions with guardrails for issue triage/labeling, docs updates, CI
troubleshooting, scheduled "nightly repair jobs", and auto-generated
`agentics-maintenance.yml` ([GitHub blog](https://github.blog/ai-and-ml/automate-repository-tasks-with-github-agentic-workflows/),
[InfoQ](https://www.infoq.com/news/2026/02/github-agentic-workflows/)). Metabase's
Repro-Bot triages bug reports by classifying, attempting reproduction in a sandbox,
and retrying up to three times ([Metabase blog](https://www.metabase.com/blog/reprobot-github-issue-triage-agent)).

**Dumb-loop autonomous engineering (Ralph).** `while :; do cat PROMPT.md |
agent; done` — progress accumulates in files and git history, never in context.
Guardrails: one item per loop, search-before-implement, test back-pressure, and
`git reset --hard` as the recovery move. Delivered a $50k-contract-grade compiler
for $297 in API cost ([ghuntley.com/ralph](https://ghuntley.com/ralph/),
[DreamHost explainer](https://www.dreamhost.com/blog/ralph-wiggum/)).

**Multi-context harness (Anthropic).** Two-phase harness: an **initializer agent**
creates `feature_list.json` (200+ features, all failing), `claude-progress.txt`,
`init.sh`, and a baseline commit; then every **coding agent** session gets bearings
from those three files, implements ONE feature, tests end-to-end via browser
automation, commits, and updates progress ([Effective harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)).
The companion post adds the **generator–evaluator split** ("separating the agent
doing the work from the agent judging it proves to be a strong lever"), pre-sprint
contracts, and active Playwright verification over static review ([Harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps))
— the architecture dream's runner already implements, now with executed-oracle
evidence.

**Sleep-time memory agents (Letta).** Background agents share memory blocks with a
primary agent and revise them asynchronously — memory compaction, archive
management, "learned context" formed while the primary is idle; primary and
sleep-time agents can use different models (fast conversational + slow/deep
curator) ([Letta sleep-time docs](https://docs.letta.com/guides/agents/architectures/sleeptime/),
[Sleep-time Compute](https://www.letta.com/blog/sleep-time-compute)).

## 2. Patterns dream lacks (or only partly has)

| Pattern | Source | dream status |
|---|---|---|
| Heartbeat reads a *checklist file* the agent itself maintains | OpenClaw `HEARTBEAT.md` | partial — ohmo's WORKSPACE STATE section is harness-rendered; letting the agent own a checklist file is one step further |
| Cron payload = *agent turn* (isolated or context-persisting session), not just argv | OpenClaw cron modes | gap — dream cron spawns argv stubs; `entry_prompt` exists but no session payload wiring |
| `HEARTBEAT_OK` suppression — proactive messages only when there's signal | OpenClaw | analog exists (skip decisions aren't surfaced to channels) |
| Initializer/first-session phase that writes the durable scaffolding | Anthropic | gap — ohmo bootstraps in Python; an *agent-driven* init session is the pattern |
| Feature list with all-failing start; one-feature-per-session | Anthropic, Ralph | analog — planner ledger; a long-horizon example should prove it across restarts |
| Sleep-time memory curation on shared memory blocks | Letta | deliberate non-goal in SDK (Model A) — but a *memory-curator example agent* fits |
| Natural-language → cron registration by the agent itself | Hermes, OpenClaw | gap — cron tools are list/show only; no `cron_create` tool |
| Skill self-creation after completing novel tasks | Hermes | seam exists (skills registry); needs an example exercising it |
| Webhook/channel delivery of scheduled results | OpenClaw | gap — events JSONL only; a channel adapter example would close it |

## 3. The deliverable: 12 example agents for `examples/`

Ranked by (a) how much of the runtime they exercise, (b) demand evidence from the
ecosystem, (c) effort. Each is ohmo-shaped: persona + a few strict tools +
workspace conventions + the stock Runtime.

**Tier 1 — high leverage, build next**

1. **`janitor` — repo maintenance agent.** Nightly cron: triage new issues,
   reproduce bugs Repro-Bot-style (bounded retries), fix lint/CI drift, propose
   dependency bumps as branches. Exercises cron→session payloads, run_task with
   oracle verification, budgets, watchdog. Demand: GitHub shipped an