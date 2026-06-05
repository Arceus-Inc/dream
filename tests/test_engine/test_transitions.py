"""Spec 03 stage 3a — internal ``TransitionBus`` for FSM observability.

Every session/turn transition fires a hook (spec 03 #15). Handlers are
observers — never gates — and a raising handler **cannot veto** the
transition (#16). This module is the in-process bus the orchestrator
calls; the public hook taxonomy in ``dream.contracts.hook`` is a separate
concern (substrate-level pre/post-tool/compact hooks) and isn't replaced
by this.

The bus is intentionally synchronous and best-effort: it logs handler
errors with traceback and returns. The orchestrator never awaits it,
never inspects its return value, and never branches on whether a
listener succeeded.
"""

from __future__ import annotations

import pytest

from dream.engine._transitions import (
    TransitionBus,
    TransitionEvent,
)

# --- TransitionEvent ---------------------------------------------------------


def test_transition_event_session_name_format() -> None:
    ev = TransitionEvent(kind="session", from_state="starting", to_state="orienting")
    assert ev.name == "session.starting.to.orienting"


def test_transition_event_turn_name_format() -> None:
    ev = TransitionEvent(kind="turn", from_state="read", to_state="plan")
    assert ev.name == "turn.read.to.plan"


def test_transition_event_is_frozen() -> None:
    ev = TransitionEvent(kind="session", from_state="starting", to_state="orienting")
    with pytest.raises((AttributeError, TypeError)):
        setattr(ev, "from_state", "working")


# --- TransitionBus: dispatch -------------------------------------------------


def test_bus_fires_listener_with_event() -> None:
    bus = TransitionBus()
    seen: list[TransitionEvent] = []
    bus.register(lambda ev: seen.append(ev))
    target = TransitionEvent(kind="session", from_state="orienting", to_state="working")
    bus.fire(target)
    assert seen == [target]


def test_bus_fires_all_listeners_in_registration_order() -> None:
    bus = TransitionBus()
    order: list[str] = []
    bus.register(lambda ev: order.append("a"))
    bus.register(lambda ev: order.append("b"))
    bus.register(lambda ev: order.append("c"))
    bus.fire(TransitionEvent(kind="turn", from_state="read", to_state="plan"))
    assert order == ["a", "b", "c"]


def test_bus_with_no_listeners_is_noop() -> None:
    bus = TransitionBus()
    # Must not raise.
    bus.fire(TransitionEvent(kind="session", from_state="starting", to_state="orienting"))


# --- TransitionBus: handler errors never veto (spec 03 #16) ------------------


def test_listener_exception_does_not_propagate() -> None:
    bus = TransitionBus()

    def boom(_ev: TransitionEvent) -> None:
        raise RuntimeError("listener broke")

    bus.register(boom)
    # Must not raise — the orchestrator never has to try/except around fire().
    bus.fire(TransitionEvent(kind="session", from_state="starting", to_state="orienting"))


def test_listener_exception_does_not_block_later_listeners() -> None:
    bus = TransitionBus()
    seen: list[str] = []

    def boom(_ev: TransitionEvent) -> None:
        raise RuntimeError("kaboom")

    bus.register(boom)
    bus.register(lambda ev: seen.append("after-boom"))
    bus.fire(TransitionEvent(kind="turn", from_state="act", to_state="verify"))
    assert seen == ["after-boom"]


def test_listener_exception_is_exposed_for_debugging() -> None:
    """Spec 03 #16: failures are observable so a buggy listener can be diagnosed,
    without taking a side-effecting logging dependency in src (Spec 00 rule 4)."""
    bus = TransitionBus()

    def boom(_ev: TransitionEvent) -> None:
        raise RuntimeError("listener crashed")

    bus.register(boom)
    bus.fire(TransitionEvent(kind="session", from_state="working", to_state="sealing"))
    assert isinstance(bus.last_exception, RuntimeError)
    assert str(bus.last_exception) == "listener crashed"


def test_bus_counts_listener_failures() -> None:
    """Visibility for tests and observability without forcing log inspection."""
    bus = TransitionBus()

    def boom(_ev: TransitionEvent) -> None:
        raise RuntimeError("x")

    bus.register(boom)
    bus.register(lambda ev: None)  # this one succeeds
    bus.register(boom)
    bus.fire(TransitionEvent(kind="session", from_state="starting", to_state="orienting"))
    assert bus.failures == 2


def test_bus_failures_accumulate_across_fires() -> None:
    bus = TransitionBus()
    bus.register(lambda ev: (_ for _ in ()).throw(RuntimeError("x")))
    bus.fire(TransitionEvent(kind="session", from_state="starting", to_state="orienting"))
    bus.fire(TransitionEvent(kind="session", from_state="orienting", to_state="working"))
    assert bus.failures == 2
