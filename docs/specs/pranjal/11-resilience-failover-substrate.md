# 11 — Resilience, Failover & Substrate

**One-liner:** Treat the model substrate as a fallible utility. Pool credentials, ladder cooldowns, switch substrates transparently. Never let a single provider outage cost a long-running task.

**Sources:** [ANT-1], [AHE] · taxonomy §19, §1

---

## Why this matters

Three failure modes account for most of the unhappy time in long-running agent operation:

1. **Rate limits.** A provider 429s mid-turn; the naive harness either dies or hammers the API and gets banned harder.
2. **Provider outages.** OpenAI is down for 40 minutes; every running task pauses; resuming is manual.
3. **Single-credential blast radius.** One key, one billing limit, one revocation event — and the entire system stops.

A harness that runs for weeks needs to treat the substrate like a flaky upstream: pool credentials, back off intelligently, fail over to a different provider without the agent noticing, and surface what's actually happening so the operator can do something about it.

This spec also defines what a *substrate* means precisely — because the same surface needs to abstract over OpenAI, Anthropic, LiteLLM, Azure OpenAI, local vLLM, and Ollama, and they don't agree on much.

## Scope

**In:** the substrate abstraction, credential pool semantics, the cooldown ladder, transparent failover, heartbeat coordination with #03, configuration model for substrates and credentials, observability for substrate health.

**Out:** the inner workings of any specific provider's API (we use SDKs); LLM-side caching (substrate-specific); the actual heartbeat *FSM* in #03; sandbox-tier network filtering (→ #08); cost tracking and forecasting (deferred).

## Assumed defaults

- **Substrate interface is small.** Every substrate exposes: `complete(prompt, params) → result`, `stream(prompt, params) → iterator`, `count_tokens(text) → int`, `max_window() → int`, `health() → {ok|degraded|down}`. Nothing else.
- **Substrate dropdown order (default):**
  1. OpenAI (`openai/gpt-*`)
  2. Anthropic (`anthropic/claude-*`)
  3. LiteLLM proxy (anything routed through a local LiteLLM)
  4. Local vLLM
  5. Local Ollama
- **Operators reorder via `.harness/substrates.toml`.** Order matters; failover proceeds top-to-bottom from the active entry.
- **One credentials file:** `.harness/credentials.toml`. Per-substrate sections, each with a *pool* of keys (not a single key). Read at runner startup; permissions checked (POSIX: 0600; Windows: owner-only ACL); refusal to start if loose.
- **Credentials pool rotates round-robin** within a substrate. A key returning a 429 or 401 is benched for the configured cooldown.
- **Three-rung cooldown ladder** per credential, per substrate:
  - Rung 1: 30 s (transient).
  - Rung 2: 5 min (sustained).
  - Rung 3: 60 min (looks broken).
  - Failures escalate one rung; successes reset to rung 0.
- **Failover trigger:** if *all* credentials for the active substrate are benched, switch to the next substrate in the dropdown. Emit a `substrate.failover` event.
- **Failover is transparent to the agent.** The agent does not see the substrate change; the runner translates parameters as needed. Where translation isn't possible (e.g. one substrate doesn't support a requested tool format), the runner refuses *that specific call* and surfaces a clear error to the agent on the next turn.
- **Heartbeat from #03 calls `health()` every 60 s** during long calls. Three consecutive `down` results → triggers failover (not session abort) if a fallback substrate is available; falls through to abort only if all fail.
- **Hard timeouts:** every substrate call has a 5-minute wall-clock cap by default. Configurable. Streaming calls measure idle time (no-new-token) not total time.
- **No silent substrate switching mid-task during long-running work** unless explicitly enabled. Default: switching is allowed only at turn boundaries, not mid-turn. Mid-turn → call fails, turn ends, next turn picks the new substrate. Override flag for tasks that must finish at any cost.
- **Substrate-specific quirks are isolated** in adapters under `.harness/substrate-adapters/`. The core harness does not branch on substrate name.

## Artefacts

### `.harness/substrates.toml`

```toml
[[substrate]]
name = "openai"
adapter = "openai_adapter.py"
models = ["gpt-5", "gpt-5-mini"]
default_model = "gpt-5"
priority = 1

[[substrate]]
name = "anthropic"
adapter = "anthropic_adapter.py"
models = ["claude-sonnet-4.5"]
default_model = "claude-sonnet-4.5"
priority = 2

[[substrate]]
name = "litellm"
adapter = "litellm_adapter.py"
base_url = "http://localhost:4000/v1"
models = ["azure-gpt-5-mini"]
priority = 3
```

### `.harness/credentials.toml`

```toml
[[openai]]
key = "sk-..."
label = "primary"

[[openai]]
key = "sk-..."
label = "secondary"

[[anthropic]]
key = "sk-ant-..."
```

(Read-only to all but the owner. Runner refuses to start if mode is wider.)

### Substrate runtime state (in-memory, mirrored to JSONL on changes)

For each `(substrate, credential)`:
- `rung` — 0, 1, 2, or 3.
- `cooldown_until` — timestamp.
- `last_error` — short string.
- `last_success` — timestamp.

### Events emitted

- `substrate.call.start` / `substrate.call.end` (the latter includes latency, tokens, success/failure).
- `substrate.credential.benched` (with rung, reason, cooldown_until).
- `substrate.credential.restored` (rung reset to 0).
- `substrate.failover` (from, to, reason).
- `substrate.health.degraded` / `substrate.health.recovered`.

## Behaviour

### Call dispatch

1. Runner selects the active substrate (top of the dropdown that has any live credential).
2. Runner selects a credential round-robin from the live pool.
3. Runner makes the call via the adapter with a 5-minute timeout.
4. On success: increment `last_success`, return result.
5. On failure: classify the error (see table below) and act per the cooldown ladder.

### Error classification

| Error class | Action |
|---|---|
| Transient (429, 5xx, network reset) | Bench credential, rung +1, retry with next live credential. |
| Auth (401, 403) | Bench credential at rung 3 immediately; emit warning event. |
| Hard refusal (400 with malformed request, content filter) | Do not bench; return error to caller. |
| Timeout (5-min cap hit) | Treat as transient. |
| Substrate-down (multiple consecutive non-auth failures across all credentials) | Trigger failover. |

### Failover

1. All credentials for substrate S benched → runner advances to S+1 in the dropdown.
2. Emit `substrate.failover` with from=S, to=S+1, reason.
3. Active substrate state updated; subsequent calls go to S+1.
4. A background task probes S's `health()` every 5 minutes; on a clean result, runner emits `substrate.health.recovered` but does *not* automatically switch back. Switch-back is operator-driven via a CLI or by waiting for the next session start.

### Heartbeat-triggered failover

- `health()` returning `down` 3 consecutive times causes the runner to:
  - If a fallback substrate exists with a live credential → failover.
  - Otherwise → abort the in-flight call, return failure to the FSM (#03), which will mark the turn as failed and either retry or end the session per the turn-timeout rules.

### Mid-turn safety

- A call that fails partway through a streaming response: runner cancels, attempts one retry on the next credential. If retry fails too, returns failure to the FSM.
- A turn that has already streamed N tokens to the agent before failure: those tokens are *not* re-played; the agent's next turn sees the failure and decides what to do.
- Default: substrate switches happen only at turn boundaries. The "must finish at any cost" override allows mid-turn substrate switches; in that case the partial output is discarded and the call is reissued to the new substrate from scratch.

### Configuration discipline

- `substrates.toml` and `credentials.toml` are read at runner startup. Changes during a session do not take effect until next start.
- Loose permissions on `credentials.toml` → refuse to start.
- Empty pool for the active substrate at startup → refuse to start (we'd just immediately fail over; better to surface this early).

## Acceptance criteria

1. **MUST** define the substrate interface as exactly `complete`, `stream`, `count_tokens`, `max_window`, `health`. New methods require a spec amendment.
2. **MUST** support credential pooling (multiple keys per substrate) and round-robin selection.
3. **MUST** refuse to start if `credentials.toml` permissions are wider than owner-only.
4. **MUST** classify substrate errors per the table above (transient / auth / hard refusal / timeout / down).
5. **MUST** implement a three-rung cooldown ladder (30 s / 5 min / 60 min).
6. **MUST** escalate a credential one rung on failure, reset to rung 0 on success.
7. **MUST** fail over to the next substrate in the dropdown when all credentials for the active substrate are benched.
8. **MUST** emit `substrate.failover` events with from/to/reason.
9. **MUST** make failover transparent to the agent at turn boundaries.
10. **MUST NOT** switch substrate mid-turn unless an explicit override is enabled for the task.
11. **MUST** enforce a default 5-minute per-call timeout (configurable).
12. **MUST** treat timeouts as transient failures (bench, escalate, retry).
13. **MUST** abort gracefully when no live credentials exist for any substrate.
14. **MUST** probe a failed-over substrate every 5 minutes and emit a `health.recovered` event when it returns clean; MUST NOT auto-switch back.
15. **MUST** keep substrate-specific quirks in adapter files, not in the runner.
16. **SHOULD** read substrate/credentials config only at startup; changes during a session are deferred to the next start.
17. **SHOULD** emit detailed `substrate.call.end` events with latency and token counts for cost/perf analysis.

## Gherkin

```gherkin
Feature: Cooldown ladder and pool rotation

  Scenario: 429 escalates credential one rung
    Given OpenAI credential "primary" is at rung 0
    When a call with that credential returns 429
    Then credential "primary" is benched
    And its rung is 1
    And cooldown_until is now + 30s

  Scenario: Round-robin skips benched credential
    Given two OpenAI credentials, "primary" benched and "secondary" live
    When the runner dispatches a call
    Then "secondary" is used
    And "primary" is not retried until cooldown_until

  Scenario: Failover when all credentials benched
    Given all OpenAI credentials are benched
    And Anthropic has a live credential
    When a new call is dispatched
    Then substrate.failover from=openai to=anthropic is emitted
    And the call goes to Anthropic

  Scenario: Failover is transparent to agent
    Given a session running on OpenAI
    When failover to Anthropic occurs between turns
    Then the agent's prompt for the next turn is unchanged
    And no special event is injected into the agent context

  Scenario: Mid-turn substrate switch refused by default
    Given a streaming call fails mid-turn
    And no override is enabled
    When the runner handles the failure
    Then the turn ends with failure
    And the next turn picks the new substrate

  Scenario: Auth error benches at rung 3 immediately
    Given a credential returns 401
    When the runner classifies the error
    Then the credential's rung is 3
    And cooldown_until is now + 60min
    And a warning event identifies the credential by label

  Scenario: Loose credentials file refuses startup
    Given .harness/credentials.toml has world-readable permissions
    When the runner starts
    Then it aborts with an error naming the file and the permission

  Scenario: Health recovery does not auto-switch
    Given the active substrate is Anthropic after failover from OpenAI
    When OpenAI's health() returns ok in a background probe
    Then a substrate.health.recovered event is emitted
    And the active substrate remains Anthropic
```

## Tests

- `test_substrate_interface_minimal` — adapter exposes exactly the five methods.
- `test_credential_pool_round_robin` — across N calls, all live credentials get used.
- `test_credentials_file_loose_perms_refuses_startup` — runner aborts on insecure file.
- `test_empty_active_pool_refuses_startup` — runner aborts if active substrate has no creds.
- `test_429_benches_at_rung_1` — single transient → rung 1.
- `test_consecutive_429s_escalate_rungs` — sequence of failures climbs the ladder.
- `test_success_resets_rung_to_zero` — single success clears prior benching.
- `test_auth_error_benches_at_rung_3` — 401 → max cooldown.
- `test_hard_refusal_does_not_bench` — 400 / content filter → no benching.
- `test_timeout_classified_as_transient` — 5-min cap hits → rung +1, retry.
- `test_round_robin_skips_benched` — benched credentials excluded from selection.
- `test_failover_when_pool_exhausted` — all benched → next substrate.
- `test_failover_event_emitted` — `substrate.failover` event present in JSONL.
- `test_failover_transparent_to_agent` — agent prompt unchanged across failover.
- `test_mid_turn_switch_refused_by_default` — failure mid-turn ends the turn.
- `test_mid_turn_switch_allowed_with_override` — override enabled → call reissued to new substrate.
- `test_no_live_substrate_aborts_session` — total exhaustion → graceful session abort.
- `test_background_probe_runs_every_5min` — probe scheduled.
- `test_health_recovered_event_emitted` — clean probe → event.
- `test_health_recovered_does_not_auto_switch` — active substrate unchanged after recovery.
- `test_per_call_timeout_default_5min` — call canceled at 5 min.
- `test_streaming_timeout_uses_idle_not_total` — long stream with regular tokens not canceled.
- `test_adapter_isolates_substrate_quirks` — runner has no `if substrate == 'openai'` branches.
- `test_call_end_event_contains_latency_and_tokens` — observability.
- `test_config_changes_during_session_deferred` — `substrates.toml` edits take effect next start.

## Edge cases

- **Same key duplicated** in the credentials file. Treated as separate pool entries (intentional — easier to reason about than dedup). Operator's choice.
- **A credential's rung-3 cooldown ends while every other credential is also rung-3.** The first one whose cooldown ends becomes live again and is selected.
- **Substrate adapter raises an unclassified exception.** Treated as transient (conservative). Logged with the full traceback for adapter-author triage.
- **Heartbeat itself fails** (network down). Counts as a `down` health result.
- **Operator pushes a `substrates.toml` change that removes the currently active substrate.** Change is deferred to next session start (per the discipline rule). If `health()` later goes down, failover proceeds using the in-memory dropdown (the one loaded at startup).
- **A model parameter the agent requested isn't supported by the failover substrate.** Runner refuses *that specific call* and surfaces a clear error to the agent on its next turn; agent decides what to do (retry with different params, end task, etc.).
- **Token-count function disagrees with substrate's billing**. Runner trusts the substrate's report on the `substrate.call.end` event for accuracy; the `count_tokens` method is a planning aid only.

## Open questions

- Should the cooldown rungs be exponential (30 s, 5 min, 60 min — current) or configurable per substrate? Some providers ratelimit much harder than others.
- Should failover have an opt-in "auto-switch back" mode? Currently switch-back is operator-driven to prevent flapping.
- Do we want per-task substrate pinning (e.g. "this task must use Claude")? Easy to add later via the exec-plan ledger; not in v1.
- Should the heartbeat call be a real `health()` ping or a no-op completion against a tiny prompt? Currently: `health()` per the adapter's definition — for local substrates, a ping; for hosted, a small completion if no health endpoint exists.

## Out of scope

- Cost tracking, forecasting, and budgeting. The events emitted here are sufficient inputs for a later cost layer.
- Provider SDK upgrades and pinning. Each adapter pins its own SDK version.
- Cross-runner credential sharing (e.g. fleet-wide rate-limit coordination). Single-host in v1.
- Speculative execution / parallel substrate calls for redundancy. Considered and deferred — adds complexity without clear payoff at our scale.
- Substrate-side caching APIs (Anthropic prompt caching, OpenAI cached input). Useful but handled by ordering in #04 — no API surface added here.
