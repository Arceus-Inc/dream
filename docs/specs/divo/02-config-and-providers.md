# 02 — Config, Providers & Model Failover

**One-liner:** Treat the model substrate as a fallible utility, not a fixed dependency:
resolve config and credentials at startup, pool keys, retry transient errors at the SDK layer,
ladder cooldowns at the credential layer, and fail over to a different provider transparently —
so a single provider 429, outage, or revocation never costs a long-running task.

**Sources (source of truth):** `docs/specs/new-specs/11-resilience-failover-substrate.md` —
the substrate abstraction, credential pool, cooldown ladder, transparent failover, and
observability defined there are carried forward in full and enriched with the config layer ·
`#00` storage layout (`~/.harness/settings.json`, provider profiles) · `#03` (the turn FSM
that consumes failures) · `#13` (sandbox network filtering — out of scope here).
**Reference (grounding only, not authority):** [openharness] `config/settings.py`
(`Settings`, `ProviderProfile`, `ResolvedAuth`, `resolve_profile`, `resolve_auth`,
`default_provider_profiles`), `config/paths.py`, `api/provider.py` (`ProviderInfo`,
`detect_provider`), `api/registry.py` (`ProviderSpec`, `PROVIDERS`,
`detect_provider_from_registry`), `api/client.py` (`AnthropicApiClient`, `ApiMessageRequest`,
`MAX_RETRIES=3`, `RETRYABLE_STATUS_CODES={429,500,502,503,529}`, exponential backoff +
jitter + `Retry-After`), `api/errors.py` (`AuthenticationFailure`, `RateLimitFailure`,
`RequestFailure`) — used to name the config and retry primitives concretely.

---

## Why this matters

Three failure modes account for most of the unhappy time in long-running autonomous
operation, and all three are *substrate* failures, not agent failures:

1. **Rate limits.** A provider 429s mid-turn; a naive harness either dies or hammers the API
   and gets banned harder.
2. **Provider outages.** A provider is down for 40 minutes; every running task pauses; resuming
   is manual.
3. **Single-credential blast radius.** One key, one billing limit, one revocation event — and
   the entire fleet stops.

A harness that runs for weeks must treat the substrate like a flaky upstream: **pool
credentials, back off intelligently, fail over without the agent noticing, and surface what is
actually happening** so the operator can act. This is the conceptual core inherited from
new-spec 11.

What that conceptual spec under-specified — and what OpenHarness shows concretely — is the
*config* layer beneath it: how providers are named, detected, and authed, and how a thin
SDK-level retry sits *inside* the credential-level cooldown ladder. This spec unifies the two
into one nested resilience model. The two layers are orthogonal and must not be conflated:

- **Inner loop (SDK retry):** a single `(substrate, credential)` call retries transient errors
  a few times with exponential backoff (OpenHarness `AnthropicApiClient`: `MAX_RETRIES=3`,
  retryable on `{429,500,502,503,529}` + network errors, honouring `Retry-After`). This
  smooths over blips *without* benching anything.
- **Outer loop (credential pool + cooldown + failover):** when a credential keeps failing past
  the inner retries, the runner benches it on a cooldown ladder; when all of a substrate's
  credentials are benched, it fails over to the next substrate. This is new-spec 11's domain.

The cost of this design is operational discipline: config and credentials are read at startup
(not hot-reloaded), credential files must be owner-only, and substrate-specific quirks must
live in adapters, never as `if substrate == "openai"` branches in the runner.

## Scope

**In:** the config schema (`Settings`, named `ProviderProfile`s, `ResolvedAuth`); provider
detection (`detect_provider`/`ProviderSpec` registry); the substrate interface; credential
pool semantics; the inner SDK-retry policy; the outer three-rung cooldown ladder; transparent
failover; heartbeat-triggered failover (coordinated with `#03`); per-call timeouts; substrate
adapters; observability events for substrate health.

**Out:** the inner workings of any specific provider's API (we use SDKs); the turn FSM itself
and how it reacts to a returned failure (`#03`); context-window sizing and prompt caching
ordering (`#04`); sandbox-tier network filtering (`#13`); cost tracking/forecasting/budgeting
(the events emitted here are inputs for a later cost layer, deferred); cross-runner /
fleet-wide rate-limit coordination (single-host v1); speculative parallel substrate calls
(considered, deferred).

## Key decisions

### Config layer

1. **Config resolves at startup from `~/.harness/settings.json` (+ env overrides).** Mirroring
   OpenHarness `config/paths.py` resolution order (env var → `~/.harness/`), `Settings` carries
   a dict of named `ProviderProfile`s plus the active selection. Changes during a session do
   **not** take effect until the next start (the same discipline new-spec 11 applies to
   `substrates.toml`/`credentials.toml`).

2. **A `ProviderProfile` is a named, self-contained provider workflow.** Following OpenHarness:
   `label`, `provider`, `api_format`, `auth_source`, `default_model`, optional `base_url`,
   `last_model`, `credential_slot`, `allowed_models`, `context_window_tokens`,
   `auto_compact_threshold_tokens`. `resolve_profile(name)` picks the active one;
   `resolved_model` collapses `last_model || default_model` through alias normalisation. The
   `context_window_tokens`/`auto_compact_threshold_tokens` fields are the handshake to `#04`.

3. **Auth is normalised to one `ResolvedAuth` shape regardless of provider.**
   `{provider, auth_kind, value, source, state}` — so the client constructor never branches on
   provider. `resolve_auth()` is the single chokepoint that turns a profile + credential store
   into `ResolvedAuth`; `auth_kind` ∈ {`api_key`, `oauth_device`, `external_oauth`} captures
   the families OpenHarness's `detect_provider` already distinguishes.

4. **Provider detection is registry-driven, not hard-coded.** A `ProviderSpec` table
   (OpenHarness `api/registry.py` `PROVIDERS`, ordered = detection priority) matches by
   model-name keyword, API-key prefix, or base-url substring, and classifies each provider by
   `backend_type` ∈ {`anthropic`, `openai_compat`, `copilot`} plus flags (`is_gateway`,
   `is_local`, `is_oauth`). `detect_provider(settings)` returns a `ProviderInfo`
   `{name, auth_kind, …}` for diagnostics. New providers are table rows, not new code paths.

### Substrate interface (from new-spec 11, verbatim intent)

5. **The substrate interface is exactly five methods.** `complete(prompt, params) → result`,
   `stream(prompt, params) → iterator`, `count_tokens(text) → int`, `max_window() → int`,
   `health() → {ok|degraded|down}`. Nothing else. A sixth method requires a spec amendment.
   This keeps OpenAI, Anthropic, LiteLLM, Azure, local vLLM, and Ollama behind one surface.

6. **Substrate-specific quirks live in adapters under `.harness/substrate-adapters/`.** The
   core runner never branches on substrate name. An adapter that raises an unclassified
   exception is treated as transient (conservative) and logged with full traceback for the
   adapter author.

### Credential pool + the two-layer resilience model

7. **One credentials file, owner-only, pools per substrate.** `.harness/credentials.toml` holds
   a *pool* of keys per substrate (not a single key), read at startup; permissions checked
   (POSIX `0600`; Windows owner-only ACL); **refuse to start if loose**. **Refuse to start if
   the active substrate's pool is empty** (better to surface early than immediately fail over).

8. **Round-robin within a substrate's live pool.** A key returning 429/401 is benched; benched
   keys are skipped until `cooldown_until`.

9. **Inner SDK retry (per call, per credential).** Before benching anything, a single call
   retries transient errors up to `MAX_RETRIES` (default 3) with exponential backoff + jitter,
   honouring `Retry-After` when present (OpenHarness `AnthropicApiClient._get_retry_delay`).
   Retryable = `{429, 500, 502, 503, 529}` + network errors. Non-retryable (auth, malformed
   request) short-circuits immediately. This is the cheap smoothing layer.

10. **Outer three-rung cooldown ladder (per credential, per substrate).** When inner retries are
    exhausted, the credential is benched: **rung 1 = 30 s** (transient), **rung 2 = 5 min**
    (sustained), **rung 3 = 60 min** (looks broken). A failure escalates one rung; a success
    resets to rung 0.

11. **Error classification drives both layers** (carried from new-spec 11):

    | Error class | Action |
    |---|---|
    | Transient (429, 5xx, network reset) | Inner-retry first; on exhaustion bench, rung +1, try next live credential. |
    | Auth (401, 403) | No inner retry. Bench at rung 3 immediately; emit warning naming the credential by label. |
    | Hard refusal (400 malformed, content filter) | Do not retry, do not bench; return error to caller. |
    | Timeout (per-call cap hit) | Treat as transient. |
    | Substrate-down (consecutive non-auth failures across all credentials) | Trigger failover. |

12. **Failover when all credentials for the active substrate are benched.** Advance to the next
    substrate in the configured dropdown order; emit `substrate.failover {from, to, reason}`.
    Order is operator-controlled via `.harness/substrates.toml` (`priority`); failover proceeds
    top-to-bottom from the active entry.

13. **Failover is transparent to the agent, at turn boundaries only.** The agent's next-turn
    prompt is unchanged; no failover event is injected into agent context. **No mid-turn
    substrate switch** unless a per-task "must finish at any cost" override is set — in which
    case partial output is discarded and the call reissued from scratch to the new substrate.
    Where a requested parameter/tool-format isn't supported by the failover substrate, the
    runner refuses *that specific call* and surfaces a clear error to the agent next turn.

14. **Heartbeat-triggered failover, coordinated with `#03`.** During long calls the heartbeat
    polls `health()` every 60 s; **three consecutive `down`** results → failover if a fallback
    substrate has a live credential, else abort the in-flight call and return failure to the
    turn FSM (which marks the turn failed and retries or ends per its timeout rules). The
    heartbeat *FSM* lives in `#03`; the health semantics live here.

15. **Per-call hard timeout, default 5 min, configurable.** Streaming calls measure **idle**
    time (no-new-token), not total wall-clock. A timeout is a transient failure (bench,
    escalate, retry).

16. **Probe-but-don't-flap on recovery.** A failed-over substrate is probed every 5 min; a clean
    `health()` emits `substrate.health.recovered` but does **not** auto-switch back. Switch-back
    is operator-driven (CLI) or happens naturally at the next session start.

## Artefact shapes (described, not coded)

### `~/.harness/settings.json` (config layer)

`Settings`: `provider`, `model`, `api_format`, `api_key?`, `base_url?`,
`profiles: {name → ProviderProfile}`, active-profile selector. Resolved via `paths.py`
accessors; `merged_profiles()` overlays user profiles on `default_provider_profiles()`.

### `ProviderProfile`

`label`, `provider`, `api_format`, `auth_source`, `default_model`, `base_url?`, `last_model?`,
`credential_slot?`, `allowed_models[]`, `context_window_tokens?`,
`auto_compact_threshold_tokens?`; property `resolved_model`.

### `ResolvedAuth`

`provider`, `auth_kind`, `value`, `source`, `state` (default `"configured"`).

### `ProviderSpec` (detection registry row)

`name`, `keywords[]`, `env_key`, `display_name`, `backend_type`, `default_base_url`,
`detect_by_key_prefix`, `detect_by_base_keyword`, `is_gateway`, `is_local`, `is_oauth`.

### `.harness/substrates.toml`

Ordered `[[substrate]]` entries: `name`, `adapter`, `models[]`, `default_model`, `priority`,
optional `base_url`. Order = failover order.

### `.harness/credentials.toml`

Per-substrate `[[<substrate>]]` arrays, each `{key, label?}`. Owner-only; runner refuses to
start if wider.

### Substrate runtime state (in-memory, mirrored to JSONL on change)

Per `(substrate, credential)`: `rung` ∈ {0,1,2,3}, `cooldown_until`, `last_error`,
`last_success`.

### Events emitted

`substrate.call.start` / `substrate.call.end` (latency, tokens, success/failure) ·
`substrate.credential.benched` (rung, reason, cooldown_until) ·
`substrate.credential.restored` · `substrate.failover` (from, to, reason) ·
`substrate.health.degraded` / `substrate.health.recovered`.

## Behaviours

### Startup resolution

1. Resolve config dir (`OPENHARNESS_CONFIG_DIR`-style env → `~/.harness/`), load `Settings`.
2. `resolve_profile()` selects the active `ProviderProfile`; `detect_provider` confirms the
   backend; `resolve_auth()` produces `ResolvedAuth`.
3. Load `substrates.toml` (dropdown order) and `credentials.toml` (pools).
4. **Refuse to start** if `credentials.toml` is loose or the active substrate's pool is empty.

### Call dispatch (nested loops)

1. Select active substrate = top of the dropdown with any live credential.
2. Select a credential round-robin from the live pool.
3. Dispatch via the adapter under the per-call timeout, **inner-retrying** transient errors up
   to `MAX_RETRIES` with backoff + jitter + `Retry-After`.
4. Success → update `last_success`, reset rung to 0, return result.
5. Inner retries exhausted → classify (table) → bench/escalate per the **outer** ladder → try
   next live credential; if none live → failover.

### Failover

All credentials for substrate S benched → advance to S+1 → emit `substrate.failover` →
subsequent calls go to S+1 → background probe of S every 5 min → on clean result emit
`health.recovered` but stay on S+1.

### Mid-turn safety

A streaming call that fails partway: cancel, one retry on the next credential; if that fails,
return failure to the FSM. Already-streamed tokens are **not** replayed — the agent's next turn
sees the failure and decides. Default: switch only at turn boundaries; override discards
partial output and reissues to the new substrate.

## Acceptance criteria

### Config & detection (MUST/SHOULD)

1. **MUST** resolve config and the active `ProviderProfile` at startup from `~/.harness/`
   (env-overridable), and **MUST** defer any config change to the next start.
2. **MUST** normalise all auth to a single `ResolvedAuth` shape so the client constructor never
   branches on provider.
3. **MUST** detect providers via the registry (keyword / key-prefix / base-url), classified by
   `backend_type`; **SHOULD** add new providers as table rows, not new code paths.
4. **MUST** carry `context_window_tokens`/`auto_compact_threshold_tokens` on the profile as the
   handshake to `#04`.

### Substrate & pooling (MUST)

5. **MUST** define the substrate interface as exactly `complete`, `stream`, `count_tokens`,
   `max_window`, `health`; a new method requires a spec amendment.
6. **MUST** support credential pooling (multiple keys per substrate) with round-robin selection
   over the *live* pool.
7. **MUST** refuse to start if `credentials.toml` permissions are wider than owner-only, or if
   the active substrate's pool is empty.
8. **MUST** keep substrate-specific quirks in adapter files, not the runner.

### Two-layer resilience (MUST)

9. **MUST** inner-retry transient errors (`{429,500,502,503,529}` + network) up to a configurable
   `MAX_RETRIES` (default 3) with exponential backoff + jitter, honouring `Retry-After`, before
   any benching.
10. **MUST NOT** inner-retry auth (401/403) or hard-refusal (400 malformed / content filter)
    errors.
11. **MUST** classify substrate errors per the table (transient / auth / hard refusal / timeout
    / down).
12. **MUST** implement the three-rung cooldown ladder (30 s / 5 min / 60 min), escalating one
    rung on failure and resetting to rung 0 on success.
13. **MUST** bench auth errors at rung 3 immediately and emit a warning naming the credential by
    label.
14. **MUST** enforce a default 5-minute per-call timeout (configurable), measuring **idle** time
    for streaming, and treat timeouts as transient.

### Failover (MUST)

15. **MUST** fail over to the next substrate in the dropdown when all credentials for the active
    substrate are benched, emitting `substrate.failover {from, to, reason}`.
16. **MUST** make failover transparent to the agent at turn boundaries and **MUST NOT** switch
    substrate mid-turn unless an explicit per-task override is enabled.
17. **MUST** abort gracefully (return failure to the FSM) when no live credential exists on any
    substrate.
18. **MUST** trigger failover (not immediate abort) after three consecutive `health()=down`
    results when a fallback with a live credential exists.
19. **MUST** probe a failed-over substrate every 5 min and emit `health.recovered` on a clean
    result, but **MUST NOT** auto-switch back.

### Observability (SHOULD)

20. **SHOULD** emit `substrate.call.end` with latency and token counts for later cost/perf
    analysis, and trust the substrate's reported token count over `count_tokens` (a planning aid
    only).

## Acceptance scenarios

```gherkin
Feature: Config resolution and provider detection

  Scenario: Config change mid-session is deferred
    Given a running session with active profile "claude-api"
    When the operator edits ~/.harness/settings.json
    Then the running session keeps using "claude-api"
    And the change takes effect only at the next start

  Scenario: Provider detected from model keyword
    Given settings with model "claude-sonnet-4-6" and no explicit provider
    When detect_provider runs
    Then ProviderInfo.name reflects the anthropic backend
    And auth_kind is resolved from the matching ProviderSpec

Feature: Two-layer resilience

  Scenario: Inner retry smooths a single 429 without benching
    Given OpenAI credential "primary" at rung 0
    When a call returns 429 once and succeeds on SDK retry
    Then "primary" is not benched
    And its rung stays 0

  Scenario: Exhausted inner retries bench the credential
    Given OpenAI credential "primary" at rung 0
    When a call returns 429 on every inner retry attempt
    Then "primary" is benched at rung 1
    And cooldown_until is now + 30s

  Scenario: Round-robin skips a benched credential
    Given OpenAI credentials "primary" (benched) and "secondary" (live)
    When the runner dispatches a call
    Then "secondary" is used
    And "primary" is not retried until cooldown_until

  Scenario: Auth error benches at rung 3 immediately
    Given a credential returns 401
    When the runner classifies the error
    Then it is not inner-retried
    And the credential's rung is 3 with cooldown_until now + 60min
    And a warning event identifies the credential by label

  Scenario: Hard refusal does not bench
    Given a credential returns 400 with a content-filter error
    Then the credential is not benched
    And the error is returned to the caller

Feature: Failover

  Scenario: Failover when all credentials benched
    Given all OpenAI credentials are benched
    And Anthropic has a live credential
    When a new call is dispatched
    Then substrate.failover from=openai to=anthropic is emitted
    And the call goes to Anthropic

  Scenario: Failover is transparent to the agent
    Given a session running on OpenAI
    When failover to Anthropic occurs between turns
    Then the agent's next-turn prompt is unchanged
    And no failover event is injected into agent context

  Scenario: Mid-turn switch refused by default
    Given a streaming call fails mid-turn and no override is enabled
    Then the turn ends with failure
    And the next turn picks the new substrate

  Scenario: Health recovery does not auto-switch
    Given the active substrate is Anthropic after failover from OpenAI
    When OpenAI's health() returns ok in a background probe
    Then substrate.health.recovered is emitted
    And the active substrate remains Anthropic

Feature: Startup discipline

  Scenario: Loose credentials file refuses startup
    Given .harness/credentials.toml is world-readable
    When the runner starts
    Then it aborts naming the file and the offending permission

  Scenario: Empty active pool refuses startup
    Given the active substrate has no credentials
    When the runner starts
    Then it aborts rather than starting and immediately failing over
```

## Tests

- `test_config_resolves_active_profile_at_startup` — profile + auth resolved once.
- `test_config_change_mid_session_deferred` — edits take effect next start.
- `test_resolved_auth_uniform_shape` — client constructor never branches on provider.
- `test_detect_provider_by_keyword` / `_by_key_prefix` / `_by_base_url` — registry detection.
- `test_new_provider_is_table_row_only` — no new code path required.
- `test_profile_carries_context_window_fields` — handshake to #04 present.
- `test_substrate_interface_minimal` — adapter exposes exactly the five methods.
- `test_credential_pool_round_robin` — all live credentials get used across N calls.
- `test_credentials_file_loose_perms_refuses_startup` — insecure file aborts.
- `test_empty_active_pool_refuses_startup` — empty pool aborts.
- `test_inner_retry_smooths_single_429` — one 429 + retry success → no bench.
- `test_inner_retry_honours_retry_after_header` — delay derived from header.
- `test_inner_retry_exhaustion_benches_at_rung_1` — retries gone → bench.
- `test_auth_error_not_inner_retried_benches_rung_3` — 401 → immediate rung 3.
- `test_hard_refusal_not_retried_not_benched` — 400/content filter → returned to caller.
- `test_consecutive_failures_escalate_rungs` — ladder climbs.
- `test_success_resets_rung_to_zero` — recovery clears benching.
- `test_timeout_classified_as_transient` — per-call cap → bench + retry.
- `test_streaming_timeout_uses_idle_not_total` — steady stream not cancelled.
- `test_round_robin_skips_benched` — benched excluded.
- `test_failover_when_pool_exhausted` — all benched → next substrate.
- `test_failover_event_emitted` — event present in JSONL.
- `test_failover_transparent_to_agent` — prompt unchanged, no injected event.
- `test_mid_turn_switch_refused_by_default` / `_allowed_with_override` — turn-boundary policy.
- `test_no_live_substrate_aborts_to_fsm` — total exhaustion → graceful failure to #03.
- `test_three_down_healthchecks_trigger_failover` — heartbeat path.
- `test_background_probe_runs_every_5min` / `test_health_recovered_event_emitted` /
  `test_health_recovered_does_not_auto_switch` — recovery without flapping.
- `test_adapter_isolates_substrate_quirks` — no `if substrate == 'openai'` in runner.
- `test_call_end_event_contains_latency_and_tokens` — observability.

## Edge cases

- **Same key duplicated** in `credentials.toml` → treated as separate pool entries
  (intentional; operator's choice).
- **All credentials at rung 3 simultaneously** → the first whose cooldown ends becomes live and
  is selected.
- **Adapter raises an unclassified exception** → treated as transient (conservative), logged
  with full traceback for the adapter author.
- **Heartbeat itself fails** (network down) → counts as a `down` health result.
- **Operator removes the active substrate from `substrates.toml` mid-session** → deferred to
  next start; if `health()` later goes down, failover uses the in-memory dropdown loaded at
  startup.
- **Requested model parameter unsupported by the failover substrate** → runner refuses *that
  call* and surfaces a clear error to the agent next turn.
- **`count_tokens` disagrees with billing** → trust the substrate's reported count on
  `call.end`; `count_tokens` is a planning aid only.

## Open questions

- Cooldown rungs fixed (30 s / 5 min / 60 min) vs configurable per substrate — some providers
  ratelimit much harder than others.
- An opt-in "auto-switch back" mode (currently operator-driven to prevent flapping).
- Per-task substrate pinning ("this task must use Claude") — easy to add later via the exec-plan
  ledger; not v1.
- Whether `health()` should be a real ping or a tiny-prompt completion — currently adapter-defined.
- Whether the inner-retry count and the cooldown ladder should share one config knob or stay
  independent (current: independent — they serve different layers).

## Out of scope

- Cost tracking, forecasting, and budgeting (the events here are sufficient inputs for a later
  cost layer).
- Provider SDK upgrades/pinning (each adapter pins its own SDK version).
- Cross-runner / fleet-wide credential and rate-limit coordination (single-host v1).
- Speculative / parallel substrate calls for redundancy (considered, deferred).
- Substrate-side caching APIs (Anthropic prompt caching, OpenAI cached input) — handled by
  prompt ordering in `#04`, no API surface added here.
- The turn FSM and heartbeat state machine that consume returned failures (→ `#03`).
- Sandbox network filtering and egress control (→ `#13`).
```
