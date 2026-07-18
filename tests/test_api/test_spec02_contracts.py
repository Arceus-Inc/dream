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
import sys
from typing import Protocol

import pytest

# All Spec 02 contracts are now implemented; the xfail scaffolding that
# protected this file during incremental delivery (Stages 1-3) is gone.
# New contract tests should be added as plain ``def test_…`` — failing
# tests are now failing tests, not expected absences.


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


# --- Decision 7 / criterion 7: startup discipline ------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits; Windows owner-only ACL check is deferred.",
)
def test_loose_credentials_file_refuses_startup(tmp_path) -> None:
    """Criterion 7: if ``credentials.toml`` is wider than owner-only, abort
    naming the file and offending permission. This is safety-critical.
    """
    import os

    from dream.api.credentials import load_credential_pools

    creds = tmp_path / "credentials.toml"
    creds.write_text('[[openai]]\nkey = "sk-test"\n', encoding="utf-8")
    os.chmod(creds, 0o644)  # group + world readable — must refuse

    with pytest.raises(Exception, match=r"(?i)permission|owner"):
        load_credential_pools(creds)


def test_empty_active_pool_refuses_startup(tmp_path) -> None:
    """Criterion 7: refuse to start with an empty pool for the active
    substrate. Better to surface early than to "start and immediately fail
    over" — that loses the operator's intent.
    """
    from dream.api.credentials import load_credential_pools

    creds = tmp_path / "credentials.toml"
    creds.write_text("", encoding="utf-8")

    with pytest.raises(Exception, match=r"(?i)empty|no credentials"):
        load_credential_pools(creds, active="openai")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows perm check fails closed before the label check is reached.",
)
def test_duplicate_credential_label_refuses_startup(tmp_path) -> None:
    """Operations address credentials by label, so two entries sharing a label
    would misroute benching/cooldown. Reject duplicates at load time."""
    import os

    from dream.api.credentials import load_credential_pools

    creds = tmp_path / "credentials.toml"
    creds.write_text(
        '[[openai]]\nkey = "sk-a"\nlabel = "dup"\n'
        '[[openai]]\nkey = "sk-b"\nlabel = "dup"\n',
        encoding="utf-8",
    )
    os.chmod(creds, 0o600)  # pass the perms check so we reach the label check

    with pytest.raises(ValueError, match=r"(?i)duplicate.*label"):
        load_credential_pools(creds)


# --- Decision 9-10: inner SDK retry vs outer cooldown are two layers ----


def test_inner_retry_smooths_single_429_without_benching() -> None:
    """Decisions 9-10: a single 429 + retry-success must not bench the
    credential. This pins the *separation* between the inner SDK-retry layer
    and the outer cooldown ladder — conflating them is the bug to prevent.
    """
    from dream.api.credentials import CredentialPool

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="transient_retried_success")

    cred = pool.get("primary")
    assert cred.rung == 0
    assert not cred.is_benched()


def test_inner_retry_exhaustion_benches_at_rung_1() -> None:
    """Decision 10: when inner retries are exhausted, bench at rung 1 (the
    30-s transient rung). One failure = one rung — not three.
    """
    from dream.api.credentials import CredentialPool

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="transient_exhausted")

    assert pool.get("primary").rung == 1


def test_auth_error_benches_at_rung_3_no_inner_retry() -> None:
    """Decision 11: 401/403 short-circuits inner retry and benches at rung 3
    immediately. Treating an auth error as transient burns through the inner
    retry budget on a request that will never succeed.
    """
    from dream.api.credentials import CredentialPool

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="auth")

    assert pool.get("primary").rung == 3


def test_hard_refusal_not_retried_not_benched() -> None:
    """Decision 11: a 400-malformed or content-filter refusal is the agent's
    bug, not the credential's — neither retry nor bench.
    """
    from dream.api.credentials import CredentialPool

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="hard_refusal")

    assert pool.get("primary").rung == 0


def test_success_resets_rung_to_zero() -> None:
    """Decision 10: a successful call resets the rung — otherwise a
    recovered credential stays benched forever after a transient blip.
    """
    from dream.api.credentials import CredentialPool

    pool = CredentialPool.from_keys("openai", ["primary"])
    pool.record_attempt("primary", outcome="transient_exhausted")
    pool.record_attempt("primary", outcome="success")

    assert pool.get("primary").rung == 0


# --- Decision 8: round-robin over live pool ------------------------------


def test_round_robin_skips_benched_credential() -> None:
    """Decision 8: a benched credential is *skipped*, not retried until its
    cooldown elapses. Otherwise the bench is purely decorative.
    """
    from dream.api.credentials import CredentialPool

    pool = CredentialPool.from_keys("openai", ["primary", "secondary"])
    pool.record_attempt("primary", outcome="auth")  # benched

    picked = [pool.pick_live() for _ in range(4)]
    assert all(c.label == "secondary" for c in picked)


# --- Decision 12-13: failover is transparent, turn-boundary only ---------


def test_failover_when_all_credentials_for_active_substrate_benched() -> None:
    """Decision 12: all active-substrate credentials benched → advance to the
    next substrate. The whole point of the cooldown ladder is to make this
    determination cheap and automatic.
    """
    from dream.api.failover import FailoverPolicy

    policy = FailoverPolicy(order=["openai", "anthropic"])
    chosen = policy.next_substrate(after="openai")
    assert chosen == "anthropic"


def test_no_live_substrate_returns_graceful_failure() -> None:
    """Criterion 17: total exhaustion returns a typed failure to the FSM —
    *not* a crash, *not* a silent retry-forever loop. The FSM (#03) decides
    whether to retry the turn or end the task.
    """
    from dream.api.failover import FailoverPolicy, NoLiveSubstrate

    policy = FailoverPolicy(order=["openai"])  # only one, exhausted
    with pytest.raises(NoLiveSubstrate):
        policy.next_substrate(after="openai")


def test_next_substrate_rejects_stale_after() -> None:
    """Logic: advance only from the true active substrate — a stale ``after``
    must not cause a no-op or backward switch that breaks chain exhaustion."""
    from dream.api.failover import FailoverPolicy

    policy = FailoverPolicy(order=["openai", "anthropic", "litellm"])
    assert policy.next_substrate(after="openai") == "anthropic"  # active advances
    with pytest.raises(ValueError, match=r"(?i)active"):
        policy.next_substrate(after="openai")  # stale — openai is no longer active


def test_failover_event_is_emitted() -> None:
    """Criterion 15: ``substrate.failover {from, to, reason}`` is observable.
    Without this event the operator can't tell why latency suddenly tripled.
    """
    from dream.api.failover import FailoverPolicy

    events: list[dict] = []
    policy = FailoverPolicy(order=["openai", "anthropic"], on_event=events.append)
    policy.next_substrate(after="openai", reason="pool_exhausted")

    assert any(e.get("type") == "substrate.failover" for e in events)


def test_mid_turn_substrate_switch_refused_by_default() -> None:
    """Decision 13 / criterion 16: switch only at turn boundaries unless the
    caller passes an explicit override. Mid-turn switching breaks tool-call
    correlation and replays cost.
    """
    from dream.api.failover import FailoverPolicy

    policy = FailoverPolicy(order=["openai", "anthropic"])
    assert policy.allow_mid_turn_switch() is False


# --- Decision 16: probe-don't-flap on recovery ---------------------------


def test_health_recovered_does_not_auto_switch_back() -> None:
    """Decision 16: a recovered substrate emits ``health.recovered`` but the
    runner stays on the failover substrate. Auto-switch-back creates a
    flapping loop the operator can't reason about.
    """
    from dream.api.failover import FailoverPolicy

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
    names = {"substrate", "provider", "provider_name"}

    def _is_ref(n: ast.expr) -> bool:
        # Catch both bare names (`provider`) and attribute access
        # (`self.provider`, `config.substrate`).
        return (isinstance(n, ast.Name) and n.id in names) or (
            isinstance(n, ast.Attribute) and n.attr in names
        )

    def _is_str(n: ast.expr) -> bool:
        return isinstance(n, ast.Constant) and isinstance(n.value, str)

    for path in runner_dir.rglob("*.py"):
        if "substrate-adapters" in path.parts or "adapters" in path.parts:
            continue  # adapters are *allowed* to branch on themselves
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            # ops has len N; operands has N+1, so (operands, operands[1:]) pairs
            # each comparison with its right-hand operand.
            for cmp_op, lhs, rhs in zip(node.ops, operands, operands[1:]):
                # Flag either ordering: `provider == "x"` and `"x" == provider`.
                if isinstance(cmp_op, ast.Eq) and (
                    (_is_ref(lhs) and _is_str(rhs)) or (_is_ref(rhs) and _is_str(lhs))
                ):
                    offenders.append(f"{path}:{node.lineno}")
    assert not offenders, f"runner branches on substrate name: {offenders}"


def test_credential_repr_does_not_leak_the_key() -> None:
    """The key is a secret: it must never appear in ``repr`` (tracebacks, logs)."""
    from dream.api.credentials import Credential

    cred = Credential(label="primary", key="sk-super-secret-123", substrate="openai")
    text = repr(cred)
    assert "sk-super-secret-123" not in text
    assert "primary" in text  # the operator-facing handle is still shown


def test_failover_force_active_validates_membership() -> None:
    from dream.api.failover import FailoverPolicy

    policy = FailoverPolicy(order=["a", "b"])
    policy.next_substrate(after="a")
    assert policy.active() == "b"
    policy.force_active("a")
    assert policy.active() == "a"
    with pytest.raises(ValueError, match="unknown substrate"):
        policy.force_active("nope")
