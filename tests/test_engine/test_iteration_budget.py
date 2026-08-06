"""``IterationBudget`` — consume/refund counter for the act-loop."""

from __future__ import annotations

import threading

from dream.engine._iteration_budget import (
    PROGRAMMATIC_TOOLS,
    IterationBudget,
    is_programmatic_only,
)


def test_consume_until_exhausted() -> None:
    budget = IterationBudget(3)
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is False
    assert budget.used == 3
    assert budget.remaining == 0


def test_refund_restores_one_slot() -> None:
    budget = IterationBudget(2)
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is False
    budget.refund()
    assert budget.used == 1
    assert budget.remaining == 1
    assert budget.consume() is True


def test_refund_never_goes_negative() -> None:
    budget = IterationBudget(1)
    budget.refund()
    assert budget.used == 0


def test_thread_safe_consume_refund_race() -> None:
    budget = IterationBudget(1000)
    errors: list[str] = []

    def worker() -> None:
        for _ in range(200):
            if budget.consume():
                budget.refund()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if budget.used < 0 or budget.used > budget.max_total:
        errors.append(f"used={budget.used}")
    assert not errors
    assert budget.used == 0


def test_is_programmatic_only_execute_code_and_spawn() -> None:
    assert is_programmatic_only({"execute_code"})
    assert is_programmatic_only({"spawn_subagent"})
    assert is_programmatic_only({"execute_code", "spawn_subagent"})
    assert not is_programmatic_only(set())
    assert not is_programmatic_only({"read_file"})
    assert not is_programmatic_only({"execute_code", "read_file"})
    assert PROGRAMMATIC_TOOLS == frozenset({"execute_code", "spawn_subagent"})
