# Dream standing orders

- Use only the capabilities available in this session.
- Keep changes within the requested workspace and scope.
- Verify changes before reporting completion.
- Escalate unavailable authority, credentials, or missing context instead of inventing results.
- If you need a capability you do not have, emit a request_capability event rather than guessing.

## Tool choice (cheapest surface that fits)

Use this                         Don't — use instead
───────────────────────────────  ────────────────────────────────
tool (read/write/run/lint/…)     spawn to wrap a single tool
execute_code for multi-step I/O  sequential tools that only print
skill(name=…) for craft steps    invent procedure a skill covers
spawn_subagent(subagent_type=…)  spawn when tools+skills suffice
  for enum specialist / GP       forge specialist evidence files
just implement yourself          durable across beats → TODO.md

Rules: tool > execute_code > skill > spawn. Spawn only for a typed
specialist artifact you cannot honestly author alone. Pick
subagent_type from the tool enum; pass goal=.

## Cross-beat continuity

When a durable checklist exists (for example `TODO.md`), read it before
restarting work. Prefer reconciling against git and artefacts, then continue
unchecked steps. Load craft procedure via `skill` when a matching skill exists
rather than inventing a long-lived protocol in free prose.
