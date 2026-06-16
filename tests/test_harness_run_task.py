"""Spec 10-H — ``Harness.run_task`` is a thin facade over ``runner.run_task``.

The Harness already exposes :meth:`run_role`; this facade adds the
analogous one-liner for the end-to-end orchestrator so callers can stay
inside the Harness namespace:

    result = await harness.run_task(
        task_id="t1",
        intent="…",
        planner=planner,
        generator_execute=gen,
        evaluator_propose=propose,
        generator_respond=respond,
        evaluator_run=evaluate,
    )

The facade:

- Forwards every kwarg to :func:`dream.runner.run_task`.
- Defaults ``worktree_root`` to ``self.config.working_dir`` so a
  Harness with a configured ``working_dir`` is a complete unit.
- Imports :func:`dream.runner.run_task` lazily — the harness <->
  runner module graph stays one-way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dream.harness import Harness, HarnessConfig


async def test_run_task_forwards_all_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))
    planner = AsyncMock()
    gen_exec = AsyncMock()
    eval_propose = AsyncMock()
    gen_respond = AsyncMock()
    eval_run = AsyncMock()

    def goal_for(step: Any, n: int) -> str:
        return "g"

    result = await harness.run_task(
        task_id="t-001",
        intent="ship it",
        planner=planner,
        generator_execute=gen_exec,
        evaluator_propose=eval_propose,
        generator_respond=gen_respond,
        evaluator_run=eval_run,
        max_sprints=3,
        verification_steps=({"kind": "shell", "command": "pytest"},),
        goal_for_step=goal_for,
    )

    assert result.task_id == "t-test"
    assert captured["task_id"] == "t-001"
    assert captured["intent"] == "ship it"
    assert captured["planner"] is planner
    assert captured["generator_execute"] is gen_exec
    assert captured["evaluator_propose"] is eval_propose
    assert captured["generator_respond"] is gen_respond
    assert captured["evaluator_run"] is eval_run
    assert captured["max_sprints"] == 3
    assert captured["verification_steps"] == (
        {"kind": "shell", "command": "pytest"},
    )
    assert captured["goal_for_step"] is goal_for


async def test_run_task_defaults_worktree_to_config_working_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)

    harness = Harness(HarnessConfig(working_dir=Path("/my/wt")))

    await harness.run_task(
        task_id="t",
        intent="i",
        planner=AsyncMock(),
        generator_execute=AsyncMock(),
        evaluator_propose=AsyncMock(),
        generator_respond=AsyncMock(),
        evaluator_run=AsyncMock(),
    )

    assert captured["worktree_root"] == Path("/my/wt")


async def test_run_task_explicit_worktree_overrides_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)

    harness = Harness(HarnessConfig(working_dir=Path("/my/wt")))

    await harness.run_task(
        task_id="t",
        intent="i",
        worktree_root=Path("/explicit"),
        planner=AsyncMock(),
        generator_execute=AsyncMock(),
        evaluator_propose=AsyncMock(),
        generator_respond=AsyncMock(),
        evaluator_run=AsyncMock(),
    )

    assert captured["worktree_root"] == Path("/explicit")


async def test_run_task_omits_unspecified_optionals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defaults belong to runner.run_task, not the facade — we forward only
    what the caller actually passed (so a future change to a default lives
    in one place). After the metering change the facade always forwards a
    UsageMeter as observer, so 'observer' IS always in captured."""
    from dream.runner._usage import UsageMeter

    captured: dict[str, Any] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))

    await harness.run_task(
        task_id="t",
        intent="i",
        planner=AsyncMock(),
        generator_execute=AsyncMock(),
        evaluator_propose=AsyncMock(),
        generator_respond=AsyncMock(),
        evaluator_run=AsyncMock(),
    )

    # The facade MUST NOT inject defaults of its own for these:
    assert "max_sprints" not in captured
    assert "verification_steps" not in captured
    assert "goal_for_step" not in captured
    # The facade ALWAYS injects a UsageMeter as observer for token metering:
    assert "observer" in captured
    assert isinstance(captured["observer"], UsageMeter)


# ----------------------------- 10-I: autowire -----------------------------


async def test_run_task_auto_wires_missing_heads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a head kwarg is None the corresponding production factory is
    invoked against the harness so a one-liner ``await harness.run_task(
    intent=...)`` wires every LLM head from the configured engine."""
    from dream.runner._usage import UsageMeter

    captured: dict[str, Any] = {}
    factory_calls: dict[str, dict[str, Any]] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    def _make_sentinel(name: str) -> Any:
        def factory(harness: Any, **kw: Any) -> str:
            factory_calls[name] = {"harness": harness, **kw}
            return f"sentinel-{name}"

        return factory

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)
    monkeypatch.setattr(
        "dream.runner.make_planner_head", _make_sentinel("planner")
    )
    monkeypatch.setattr(
        "dream.runner.make_generator_head", _make_sentinel("generator")
    )
    monkeypatch.setattr(
        "dream.runner.make_evaluator_propose_head",
        _make_sentinel("evaluator_propose"),
    )
    monkeypatch.setattr(
        "dream.runner.make_generator_respond_head",
        _make_sentinel("generator_respond"),
    )
    monkeypatch.setattr(
        "dream.runner.make_evaluator_head", _make_sentinel("evaluator")
    )

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))
    await harness.run_task(task_id="t", intent="i")

    assert captured["planner"] == "sentinel-planner"
    assert captured["generator_execute"] == "sentinel-generator"
    assert captured["evaluator_propose"] == "sentinel-evaluator_propose"
    assert captured["generator_respond"] == "sentinel-generator_respond"
    assert captured["evaluator_run"] == "sentinel-evaluator"

    for name in (
        "planner",
        "generator",
        "evaluator_propose",
        "generator_respond",
        "evaluator",
    ):
        assert factory_calls[name]["harness"] is harness
        assert factory_calls[name]["harness_dir"] is None
        # The facade passes the UsageMeter (wrapping None) as observer to
        # head factories so they can meter head events too.
        assert isinstance(factory_calls[name]["observer"], UsageMeter)


async def test_run_task_mints_task_id_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import re

    captured: dict[str, Any] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))
    await harness.run_task(
        intent="i",
        planner=AsyncMock(),
        generator_execute=AsyncMock(),
        evaluator_propose=AsyncMock(),
        generator_respond=AsyncMock(),
        evaluator_run=AsyncMock(),
    )

    assert re.fullmatch(r"t-\d{8}T\d{6}-[0-9a-f]{4}", captured["task_id"])


async def test_run_task_explicit_heads_override_factory_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("factory must not run when head is supplied")

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)
    monkeypatch.setattr("dream.runner.make_planner_head", _boom)
    monkeypatch.setattr("dream.runner.make_generator_head", _boom)
    monkeypatch.setattr("dream.runner.make_evaluator_propose_head", _boom)
    monkeypatch.setattr("dream.runner.make_generator_respond_head", _boom)
    monkeypatch.setattr("dream.runner.make_evaluator_head", _boom)

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))
    planner = AsyncMock()
    gen_exec = AsyncMock()
    eval_propose = AsyncMock()
    gen_respond = AsyncMock()
    eval_run = AsyncMock()
    await harness.run_task(
        task_id="t",
        intent="i",
        planner=planner,
        generator_execute=gen_exec,
        evaluator_propose=eval_propose,
        generator_respond=gen_respond,
        evaluator_run=eval_run,
    )

    assert captured["planner"] is planner
    assert captured["generator_execute"] is gen_exec
    assert captured["evaluator_propose"] is eval_propose
    assert captured["generator_respond"] is gen_respond
    assert captured["evaluator_run"] is eval_run


async def test_run_task_forwards_observer_to_runner_and_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dream.runner._observer import _CapturingObserver
    from dream.runner._usage import UsageMeter

    captured: dict[str, Any] = {}
    factory_observers: dict[str, Any] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    def _make_sentinel(name: str) -> Any:
        def factory(harness: Any, **kw: Any) -> str:
            factory_observers[name] = kw.get("observer")
            return f"sentinel-{name}"

        return factory

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)
    for fn in (
        "make_planner_head",
        "make_generator_head",
        "make_evaluator_propose_head",
        "make_generator_respond_head",
        "make_evaluator_head",
    ):
        monkeypatch.setattr(
            f"dream.runner.{fn}", _make_sentinel(fn)
        )

    observer = _CapturingObserver()
    harness = Harness(HarnessConfig(working_dir=Path("/wt")))
    await harness.run_task(task_id="t", intent="i", observer=observer)

    # The facade wraps the caller observer in a UsageMeter; the meter is
    # forwarded to both the runner and the head factories.
    assert isinstance(captured["observer"], UsageMeter)
    assert captured["observer"]._inner is observer
    for fn in (
        "make_planner_head",
        "make_generator_head",
        "make_evaluator_propose_head",
        "make_generator_respond_head",
        "make_evaluator_head",
    ):
        assert isinstance(factory_observers[fn], UsageMeter)
        assert factory_observers[fn]._inner is observer


# ----------------------------- token metering: piece 5 --------------------


def _make_minimal_run_task_result() -> Any:
    """Build a minimal RunTaskResult for use in fake runners."""
    from dream.planner import LedgerStep, PlannerLedger
    from dream.runner._run import RunTaskResult

    ledger = PlannerLedger(
        task_id="t-test",
        intent="test intent",
        created_at=0.0,
        steps=(LedgerStep(id="s1", description="do it"),),
    )
    return RunTaskResult(
        task_id="t-test",
        spec_path=Path("/wt/spec.md"),
        ledger_path=Path("/wt/ledger.json"),
        final_ledger=ledger,
        sprints=(),
        events=(),
    )


def test_run_task_result_default_usage_by_model_is_empty() -> None:
    """RunTaskResult.usage_by_model defaults to empty mapping."""
    result = _make_minimal_run_task_result()
    assert result.usage_by_model == {}


async def test_run_task_omits_unspecified_optionals_updated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the metering change the facade ALWAYS forwards a UsageMeter as observer,
    so 'observer' is now always in captured. max_sprints/verification_steps/goal_for_step
    are still NOT forwarded when not given."""
    from dream.runner._usage import UsageMeter

    captured: dict[str, Any] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_minimal_run_task_result()

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))

    await harness.run_task(
        task_id="t",
        intent="i",
        planner=AsyncMock(),
        generator_execute=AsyncMock(),
        evaluator_propose=AsyncMock(),
        generator_respond=AsyncMock(),
        evaluator_run=AsyncMock(),
    )

    # After metering: observer is always forwarded (as UsageMeter)
    assert "observer" in captured
    assert isinstance(captured["observer"], UsageMeter)

    # These must still NOT be injected when not supplied
    assert "max_sprints" not in captured
    assert "verification_steps" not in captured
    assert "goal_for_step" not in captured


async def test_run_task_populates_usage_by_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Facade wires a UsageMeter, fires role.session.closed events through it,
    and stitches usage_by_model into RunTaskResult."""
    from dream.runner._run import RunTaskResult

    async def _fake_run_task(**kwargs: Any) -> RunTaskResult:
        # Fire two role.session.closed events for model "gpt-x"
        observer = kwargs["observer"]
        observer.on_event(
            {
                "kind": "role.session.closed",
                "model": "gpt-x",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            }
        )
        observer.on_event(
            {
                "kind": "role.session.closed",
                "model": "gpt-x",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 7,
                    "cache_read_tokens": 3,
                    "cache_write_tokens": 0,
                },
            }
        )
        return _make_minimal_run_task_result()

    monkeypatch.setattr("dream.runner._run.run_task", _fake_run_task)

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))

    result = await harness.run_task(
        task_id="t-001",
        intent="ship it",
        planner=AsyncMock(),
        generator_execute=AsyncMock(),
        evaluator_propose=AsyncMock(),
        generator_respond=AsyncMock(),
        evaluator_run=AsyncMock(),
    )

    assert "gpt-x" in result.usage_by_model
    snap = result.usage_by_model["gpt-x"]
    assert snap.input_tokens == 30
    assert snap.output_tokens == 12
    assert snap.cache_read_tokens == 3
    assert snap.total_tokens == 42
