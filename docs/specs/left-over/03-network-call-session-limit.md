# 03 — Network-call session limit (left over from #13D)

**Deferred from:** `#13` slice 13D (session limits). Tokens and tool-calls ship;
this is the third counter.
**Status:** not built — blocked on an egress chokepoint (net-allowlist enforcement).

## What was deferred

Spec 13 decision #8 / AC #18 names three per-session caps:
`max_llm_tokens_per_session`, `max_tool_calls_per_session`, and
`max_network_calls_per_session` (default 500), each aborting the session with
`limit-exceeded:{counter}` on breach. 13D shipped the **token** and
**tool-call** counters (both have exact chokepoints in the act-loop —
`complete.usage` per turn, `dispatch` per call). The **network-call** counter is
this left-over.

## Why it was deferred

A network-call count is only truthful if every outbound request passes through a
single observable point. The harness has none yet:

- `bash` running `curl`/`wget`/`pip` makes requests from a **subprocess** the
  harness cannot see into.
- An MCP tool call may make zero, one, or many requests.

The only hook available today is a tool's declared `network_host`
(`BaseTool.effects_for`), which MCP/web tools could set but `bash` does **not** —
so a counter built now would silently miss the dominant egress vector (subprocess
network use), giving false "under the cap" assurance. The spec itself anchors the
count on the substrate: *"the runner trusts the substrate's own usage report"*
(13 §"Limit enforcement"). That substrate is the deferred net-allowlist /
sandbox-network layer.

The stable API already exists: `SessionLimits.max_network_calls` and
`SessionLimiter.record_network_call()` ship in 13D but are not auto-incremented.

## Scope (when built)

- The **net-allowlist enforcement** layer (a later `#13` slice) is the egress
  chokepoint: as each outbound request is allow/deny-checked against
  `.harness/net-allowlist.toml`, it calls `record_network_call()`.
- On breach, the session aborts with `SessionEnd(reason="limit-exceeded:network_calls")`,
  reusing the same path as the token / tool-call counters.
- Reconcile against the substrate's own egress report at session end and surface
  drift (13 §"Limit enforcement").

## Acceptance criteria

1. **MUST** increment a per-session network-call counter at the egress chokepoint
   (net-allowlist enforcement), covering subprocess egress — not only tools that
   self-declare a `network_host`.
2. **MUST** abort with `limit-exceeded:network_calls` on breach, reusing the 13D
   `SessionLimiter`/`SessionEnd` path.
3. **MUST** reset per session (no roll-forward), like the other counters.

## Dependencies

- Net-allowlist enforcement / sandbox-network layer (the egress chokepoint).
  **Blocking.**
- `#13D` `SessionLimiter` (`record_network_call` + `max_network_calls`) — shipped,
  the consumer is ready.
