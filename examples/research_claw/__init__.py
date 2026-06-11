"""research_claw — a mini AutoResearchClaw on the dream runtime.

A deterministic staged pipeline that turns one research *idea* into a
complete, **experimentally tested** paper. It mirrors the structure of
AutoResearchClaw (aiming-lab/AutoResearchClaw) — scope → literature →
experiment design + code → execution → analysis → paper → review — but
collapses its 23 stages into 6 and keeps each stage a single focused
session.

The differentiator from a fake-paper generator is the same one
AutoResearchClaw and dream's spec-15 oracle insist on: **the experiment
really runs.** The orchestrator executes the generated code itself in
dream's ``SubprocessSandbox`` and the numbers in the paper come from that
authoritative run — not from the model's claim that it ran.

Run::

    DREAM_API_KEY=... DREAM_MODEL=... python examples/research_claw/agent.py \
        --idea "Momentum SGD converges faster than plain SGD on a convex quadratic" \
        --workspace ~/paper-lab
"""
