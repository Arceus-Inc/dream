"""Unit tests for dream.spawn._context — SpawnBudget, SpawnContext, read_spawn_context.

Tests written FIRST (RED), before implementation exists.
"""

from __future__ import annotations

from dream.spawn._context import (
    MAX_SPAWNS_PER_SESSION,
    SPAWN_CONTEXT_KEY,
    SpawnBudget,
    SpawnContext,
    read_spawn_context,
)

# --- SpawnBudget -----------------------------------------------------------


def test_budget_acquire_returns_true_while_under_cap() -> None:
    budget = SpawnBudget(cap=3)
    assert budget.acquire() is True
    assert budget.acquire() is True
    assert budget.acquire() is True


def test_budget_acquire_returns_false_at_cap() -> None:
    budget = SpawnBudget(cap=2)
    budget.acquire()
    budget.acquire()
    assert budget.acquire() is False


def test_budget_used_tracks_successful_acquires_only() -> None:
    budget = SpawnBudget(cap=2)
    budget.acquire()
    budget.acquire()
    budget.acquire()  # over cap — must not count
    assert budget.used == 2


def test_budget_zero_cap_always_refuses() -> None:
    budget = SpawnBudget(cap=0)
    assert budget.acquire() is False
    assert budget.used == 0


def test_budget_default_cap_is_max_spawns_per_session() -> None:
    budget = SpawnBudget()
    assert budget.cap == MAX_SPAWNS_PER_SESSION


def test_max_spawns_per_session_constant_is_16() -> None:
    assert MAX_SPAWNS_PER_SESSION == 16


def test_budget_17th_acquire_refused() -> None:
    """Spec pin: the 17th call must return False when cap=16."""
    budget = SpawnBudget(cap=MAX_SPAWNS_PER_SESSION)
    for _ in range(MAX_SPAWNS_PER_SESSION):
        assert budget.acquire() is True
    assert budget.acquire() is False


# --- read_spawn_context ---------------------------------------------------


def test_read_spawn_context_returns_none_when_absent() -> None:
    assert read_spawn_context({}) is None


def test_read_spawn_context_returns_none_for_wrong_type() -> None:
    assert read_spawn_context({SPAWN_CONTEXT_KEY: "oops"}) is None


def test_read_spawn_context_returns_context_when_present() -> None:
    async def _fake_spawn(*args: object, **kwargs: object) -> object:
        return None

    ctx = SpawnContext(
        spawn=_fake_spawn,
        budget=SpawnBudget(),
    )
    metadata: dict[str, object] = {SPAWN_CONTEXT_KEY: ctx}
    assert read_spawn_context(metadata) is ctx


def test_read_spawn_context_ignores_other_keys() -> None:
    meta: dict[str, object] = {"unrelated": 42}
    assert read_spawn_context(meta) is None


# --- SpawnContext fields --------------------------------------------------


def test_spawn_context_emit_defaults_to_none() -> None:
    async def _spawn(*args: object, **kwargs: object) -> object:
        return None

    ctx = SpawnContext(spawn=_spawn, budget=SpawnBudget())
    assert ctx.emit is None


def test_spawn_context_fire_subagent_stop_defaults_to_none() -> None:
    async def _spawn(*args: object, **kwargs: object) -> object:
        return None

    ctx = SpawnContext(spawn=_spawn, budget=SpawnBudget())
    assert ctx.fire_subagent_stop is None


def test_spawn_context_accepts_emit_and_fire() -> None:
    async def _spawn(*args: object, **kwargs: object) -> object:
        return None

    recorded: list[dict[str, object]] = []

    def _my_emit(event: dict[str, object]) -> None:
        recorded.append(event)

    async def _fire(payload: dict[str, object]) -> None:
        pass

    ctx = SpawnContext(
        spawn=_spawn,
        budget=SpawnBudget(),
        emit=_my_emit,
        fire_subagent_stop=_fire,
    )
    assert ctx.emit is _my_emit
    assert ctx.fire_subagent_stop is _fire
