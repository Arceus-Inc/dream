"""Spec 02 — contract pins for the substrate / credential / failover surface.

The substrate interface, credential pool, two-layer resilience model, and
transparent failover described in ``docs/specs/divo/02-config-and-providers.md``
aren't built yet. Rather than wait for the implementation to land before
writing acceptance tests — which is when contracts drift — this file pins the
load-bearing *shape* of the spec **today**, as :pyfunc:`pytest.mark.xfail`
with ``strict=True``.

Each test fails today (typically ``ImportError`` against a module that doesn't
yet exist, occasionally an ``AttributeError`` on a partial implementation).
``xfail(strict=True)`` makes that the expected outcome — but the moment the
implementation lands and a test starts passing, pytest reports XPASS, which
strict mode promotes to a hard failure, forcing the author to remove the
decorator and acknowledge that the contract is now met. The xfails are
therefore living checklist items for the Spec 02 implementor, not dead code.

What is *not* pinned here:

- per-rung cooldown durations (30 s / 5 min / 60 min) — these are configurable
  per Spec 02 open question, and pinning magic numbers would force churn the
  moment the operator-tuning surface lands.
- ``MAX_RETRIES=3``, ``RETRYABLE_STATUS_CODES`` — same reason; the spec calls
  them "default 3" / "configurable".
- The exact event payload schema — emitted, yes; field-level shape is a job
  for ``test_events.py`` once the event module exists.
- Heartbeat / FSM coupling — owned by Spec 03.

Module paths are guesses consistent with the existing dream layout
(``src/dream/api/`` and ``src/dream/config/`` carry stubs today). When the
implementor picks different names they only need to retarget the imports —
the *contracts* asserted below are spec-derived and stable.
"""

from __future__ import annotations

import inspect
from typing import Protocol, get_type_hints

import pytest

# Strict-mode xfail: the test is expected to fail today (mostly ImportError
# against modules that don't exist yet) and the moment it starts passing,
# pytest will flip it to a hard failure so the decorator gets removed.
_PENDING = pytest.mark.xfail(
    strict=True,
    reason="Spec 02 implementation pending — see docs/specs/divo/02-config-and-providers.md",
)


# --- Decision 5: substrate interface is exactly five methods --------------


def test_substrate_interface_is_exactly_five_methods() -> None:
    """Spec 02 decision 5: ``complete``, ``stream``, ``count_tokens``,
    ``max_window``, ``health`` — and nothing else. A sixth method "requires a
    spec amendment".
    """
    # The implementor may name this ``Substrate`` (preferred per the spec)
    # or fold it into the existing Provider Protocol — either way the
    # five-method shape must be discoverable from one symbol.
    from dream.api.substrate import Substrate

    expected = {"complete", "stream", "count_tokens", "max_window", "health"}
    actual = {
        name
        for name, _ in inspect.getmembers(Substrate, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert actual == expected, (
        f"substrate surface drifted: missing={expected - actual} extra={actual - expected}"
    )


def test_substrate_is_a_protocol() -> None:
    """A Protocol (not an ABC) so adapters in ``.dream/substrate-adapters/``
    can satisfy it structurally without inheritance — decision 6.
    """
    from dream.api.substrate import Substrate

    assert issubclass(Substrate, Protocol)


# --- Decision 3: ResolvedAuth uniform shape --------------------------------


def test_resolved_auth_uniform_shape() -> None:
    """All auth normalises to one shape so the client constructor never
    branches on provider. Spec 02 decision 3 enumerates exactly these fields.
    """
    from dream.config.from_file import ResolvedAuth

    hints = get_type_hints(ResolvedAuth)
    expected = {"provider", "auth_kind", "value", "source", "state"}
    assert expected.issubset(set(hints)), f"missing fields: {expected - set(hints)}"


def test_resolved_auth_kind_covers_documented_families() -> None:
    """``auth_kind`` ∈ {api_key, oauth_device, external_oauth} — decision 3."""
    from dream.config.from_file import ResolvedAuth

    samples = [
        ResolvedAuth(provider="openai", auth_kind="api_key", value="x", source="env"),
        ResolvedAuth(provider="anthropic", auth_kind="oauth_device", value="x", source="env"),
        ResolvedAuth(provider="copilot", auth_kind="external_oauth", value="x", source="env"),
    ]
    assert {a.auth_kind for a in samples} == {"api_key", "oauth_device", "external_oauth"}


# --- Decision 2 / criterion 4: ProviderProfile + #04 handshake -----------


def test_provider_profile_carries_context_window_handshake_fields() -> None:
    """Decision 2 + criterion 4: the profile carries ``context_window_tokens``
    and ``auto_compact_threshold_tokens`` as the handshake to Spec 04.
    Without these, the context-budgeting layer has no input.
    """
    from dream.config.from_file import ProviderProfile

    hints = get_type_hints(ProviderProfile)
    assert "context_window_tokens" in hints
    assert "auto_compact_threshold_tokens" in hints


def test_provider_profile_has_documented_core_fields() -> None:
    """Decision 2 enumerates the minimum field set."""
    from dream.config.from_file import ProviderProfile

    hints = get_type_hints(ProviderProfile)
    required = {"label", "provider", "api_format", "auth_source", "default_model"}
    assert required.issubset(set(hints)), f"missing: {required - set(hints)}"


# --- Decision 4: registry-driven provider detection -----------------------


def test_provider_spec_registry_is_an_ordered_table() -> None:
    """Decision 4: order = detection priority. A *new* provider is added as a
    table row, not a new code path. A list (ordered) — not a dict — makes the
    priority explicit.
    """
    from dream.api._registry import PROVIDERS

    assert isinstance(PROVIDERS, list)
    assert len(PROVIDERS) > 0


def test_detect_provider_by_model_keyword() -> None:
    """Decision 4 + criterion 3: detection by model-name keyword."""
    from dream.api._registry import detect_provider

    info = detect_provider(model="claude-sonnet-4-6")
    assert info is not None
    assert "anthropic" in info.name.lower() or info.backend_type == "anthropic"


def test_detect_provider_by_key_prefix() -> None:
    from dream.api._registry import detect_provider

    info = detect_provider(api_key="sk-ant-test-key")
    assert info is not None
    assert "anthropic" in info.name.lower() or info.backend_type == "anthropic"


def test_detect_provider_by_base_url() -> None:
    from dream.api._registry import detect_provider

    info = detect_provider(base_url="https://api.anthropic.com")
    assert info is not None
    assert "anthropic" in info.name.lower() or info.backend_type == "anthropic"


# --- Decision 7 / criterion 7: startup discipline ------------------------


@_PENDING
def test_loose_credentials_file_refuses_startup(tmp_path) -> None:
    """Criterion 7: if ``credentials.toml`` is wider than owner-only, abort
    naming the file and offending permission. This is safety-critical.
    """
    import os

    from dream.api.credentials import load_credential_pools  # type: ignore[import-not-found]

    creds = tmp_path / "credentials.toml"
    creds.write_text('[[openai]]\nkey = "sk-test"\n', encoding="utf-8")
    os.chmod(creds, 0o644)  # group + world readable — must refuse

    with pytest.raises(Exception, match=r"(?i)permission|owner"):
        load_credential_pools(creds)


@_PENDING
def test_empty_active_pool_refuses_startup(tmp_path) -> None:
    """Criterion 7: refuse to start with an empty pool for the active
    substrate. Better to surface early than to "start and immediately fail
    over" — that loses the operator's intent.
    """
    from dream.api.credentials import load_credential_pools  # type: ignore[import-not-found]

    creds = tmp_path / "credentials.toml"
    creds.write_text("", encoding="utf-8")

    with pytest.raises(Exception, match=r"(?i)empty|no credentials"):
        load_credential_pools(creds, active="openai")


# --- Decision 9-10: inner SDK retry vs outer cooldown are two layers ----


@_PENDING
def test_inner_retry_smooths_single_429_without_benching() -> None:
    """Decisions 9-10: a single 429 + retry-success must not bench the
    credential. This pins the *separation* between the inner SDK-retry layer
    and the outer cooldown ladder — conflating them is the bug to prevent.
    """
    from dream.api.credentials import CredentialPool  # type: ignore[import-not-found]

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="transient_retried_success")

    cred = pool.get("primary")
    assert cred.rung == 0
    assert not cred.is_benched()


@_PENDING
def test_inner_retry_exhaustion_benches_at_rung_1() -> None:
    """Decision 10: when inner retries are exhausted, bench at rung 1 (the
    30-s transient rung). One failure = one rung — not three.
    """
    from dream.api.credentials import CredentialPool  # type: ignore[import-not-found]

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="transient_exhausted")

    assert pool.get("primary").rung == 1


@_PENDING
def test_auth_error_benches_at_rung_3_no_inner_retry() -> None:
    """Decision 11: 401/403 short-circuits inner retry and benches at rung 3
    immediately. Treating an auth error as transient burns through the inner
    retry budget on a request that will never succeed.
    """
    from dream.api.credentials import CredentialPool  # type: ignore[import-not-found]

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="auth")

    assert pool.get("primary").rung == 3


@_PENDING
def test_hard_refusal_not_retried_not_benched() -> None:
    """Decision 11: a 400-malformed or content-filter refusal is the agent's
    bug, not the credential's — neither retry nor bench.
    """
    from dream.api.credentials import CredentialPool  # type: ignore[import-not-found]

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="hard_refusal")

    assert pool.get("primary").rung == 0


@_PENDING
def test_success_resets_rung_to_zero() -> None:
    """Decision 10: a successful call resets the rung — otherwise a
    recovered credential stays benched forever after a transient blip.
    """
    from dream.api.credentials import CredentialPool  # type: ignore[import-not-found]

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="transient_exhausted")
    pool.record_attempt("primary", outcome="success")

    assert pool.get("primary").rung == 0


# --- Decision 8: round-robin over live pool ------------------------------


@_PENDING
def test_round_robin_skips_benched_credential() -> None:
    """Decision 8: a benched credential is *skipped*, not retried until its
    cooldown elapses. Otherwise the bench is purely decorative.
    """
    from dream.api.credentials import CredentialPool  # type: ignore[import-not-found]

    pool = CredentialPool.from_keys("openai", ["primary", "secondary"])
    pool.record_attempt("primary", outcome="auth")  # benched

    picked = [pool.pick_live() for _ in range(4)]
    assert all(c.label == "secondary" for c in picked)


# --- Decision 12-13: failover is transparent, turn-boundary only ---------


@_PENDING
def test_failover_when_all_credentials_for_active_substrate_benched() -> None:
    """Decision 12: all active-substrate credentials benched → advance to the
    next substrate. The whole point of the cooldown ladder is to make this
    determination cheap and automatic.
    """
    from dream.api.failover import FailoverPolicy  # type: ignore[import-not-found]

    policy = FailoverPolicy(order=["openai", "anthropic"])
    chosen = policy.next_substrate(after="openai")
    assert chosen == "anthropic"


@_PENDING
def test_no_live_substrate_returns_graceful_failure() -> None:
    """Criterion 17: total exhaustion returns a typed failure to the FSM —
    *not* a crash, *not* a silent retry-forever loop. The FSM (#03) decides
    whether to retry the turn or end the task.
    """
    from dream.api.failover import FailoverPolicy, NoLiveSubstrate  # type: ignore[import-not-found]

    policy = FailoverPolicy(order=["openai"])  # only one, exhausted
    with pytest.raises(NoLiveSubstrate):
        policy.next_substrate(after="openai")


@_PENDING
def test_failover_event_is_emitted() -> None:
    """Criterion 15: ``substrate.failover {from, to, reason}`` is observable.
    Without this event the operator can't tell why latency suddenly tripled.
    """
    from dream.api.failover import FailoverPolicy  # type: ignore[import-not-found]

    events: list[dict] = []
    policy = FailoverPolicy(order=["openai", "anthropic"], on_event=events.append)
    policy.next_substrate(after="openai", reason="pool_exhausted")

    assert any(e.get("type") == "substrate.failover" for e in events)


@_PENDING
def test_mid_turn_substrate_switch_refused_by_default() -> None:
    """Decision 13 / criterion 16: switch only at turn boundaries unless the
    caller passes an explicit override. Mid-turn switching breaks tool-call
    correlation and replays cost.
    """
    from dream.api.failover import FailoverPolicy  # type: ignore[import-not-found]

    policy = FailoverPolicy(order=["openai", "anthropic"])
    assert policy.allow_mid_turn_switch() is False


# --- Decision 16: probe-don't-flap on recovery ---------------------------


@_PENDING
def test_health_recovered_does_not_auto_switch_back() -> None:
    """Decision 16: a recovered substrate emits ``health.recovered`` but the
    runner stays on the failover substrate. Auto-switch-back creates a
    flapping loop the operator can't reason about.
    """
    from dream.api.failover import FailoverPolicy  # type: ignore[import-not-found]

    policy = FailoverPolicy(order=["openai", "anthropic"])
    policy.next_substrate(after="openai", reason="pool_exhausted")
    assert policy.active() == "anthropic"

    policy.record_probe("openai", healthy=True)
    assert policy.active() == "anthropic"  # still on the failover substrate


# --- Architectural lint: no `if substrate == "name"` branches in runner --


def test_runner_does_not_branch_on_substrate_name() -> None:
    """Decision 8 (criterion 8): substrate-specific quirks live in adapters.
    The runner must not contain ``if substrate == "openai":`` branches —
    every such branch is a hidden coupling that defeats the whole adapter
    abstraction.

    Passes vacuously today (the runner is mostly stubs) — the point is to
    *prevent* the easy regression once the substrate layer is wired in.
    """
    import ast
    from pathlib import Path

    runner_dir = Path(__file__).resolve().parents[2] / "src" / "dream" / "api"
    offenders: list[str] = []
    for path in runner_dir.rglob("*.py"):
        if "substrate-adapters" in path.parts or "adapters" in path.parts:
            continue  # adapters are *allowed* to branch on themselves
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
                if node.left.id in {"substrate", "provider", "provider_name"}:
                    for cmp_op, comparator in zip(node.ops, node.comparators, strict=False):
                        if isinstance(cmp_op, ast.Eq) and isinstance(comparator, ast.Constant):
                            offenders.append(f"{path}:{node.lineno}")
    assert not offenders, f"runner branches on substrate name: {offenders}"
