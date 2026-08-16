"""``run_task(session_scope=...)`` — one resumable thread per role.

A control plane runs a task in short windows and needs the roles to pick up
where they left off. One scope key names the whole task; each role thread hangs
off it, so the caller keeps a single key instead of one per role.

Each role has exactly one head, so the mapping is direct: planner, generator
and evaluator each get a thread named after the scope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from dream.harness import Harness, HarnessConfig
from dream.planner import LedgerStep
from dream.roles import RoleName
from dream.runner import RunRoleResult, make_generator_head
from dream.runner.role import role_session_id
from dream.session import SessionCost, SessionOptions

HEAD_FACTORIES = (
    "make_planner_head",
    "make_generator_head",
    "make_evaluator_head",
)


class _RecordingHarness:
    """Stands in for a Harness, recording the thread each head asks to run in."""

    def __init__(self, reply: str = "done") -> None:
        self._reply = reply
        self.calls: list[tuple[str, str | None]] = []

    async def run_role(
        self,
        role: RoleName | str,
        intent: str,
        *,
        options: SessionOptions | None = None,
        harness_dir: Path | None = None,
        observer: Any = None,
        session_id: str | None = None,
        resume_messages: object = None,
    ) -> RunRoleResult:
        self.calls.append((str(role), session_id))
        return RunRoleResult(
            role=cast(RoleName, role),
            session_id=session_id or "unnamed",
            final_text=self._reply,
            cost=SessionCost(),
            events=(),
        )


def _as_harness(stub: _RecordingHarness) -> Harness:
    return cast(Harness, stub)


def test_role_session_id_namespaces_the_role_under_the_scope() -> None:
    assert role_session_id("task-42", "generator") == "task-42-generator"


def test_role_session_id_stays_usable_as_a_path_segment() -> None:
    """A session id names a sidecar directory, so it has to survive that root's validator.

    ``:`` reads as Windows drive / alternate-data-stream syntax there. Deriving
    ids with one used to blow up inside engine construction as a baffling
    "unsafe task_id", and only against a real engine — every faked-engine test
    sailed past it.
    """
    from dream.utils.identifiers import checked_task_id

    assert checked_task_id(role_session_id("task-42", "planner")) == "task-42-planner"


def test_role_session_id_refuses_a_scope_that_cannot_be_a_path_segment() -> None:
    """A caller-supplied scope fails where it is set, not deep in the engine."""
    with pytest.raises(ValueError, match="unsafe session_id"):
        role_session_id("task:42", "planner")


async def test_run_task_hands_the_scope_to_every_autowired_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls: dict[str, dict[str, Any]] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        return _minimal_result()

    def _sentinel(name: str) -> Any:
        def factory(harness: Any, **kw: Any) -> str:
            factory_calls[name] = kw
            return f"sentinel-{name}"

        return factory

    monkeypatch.setattr("dream.runner.task.run_task", _fake_run_task)
    for name in HEAD_FACTORIES:
        monkeypatch.setattr(f"dream.runner.{name}", _sentinel(name))

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))
    await harness.run_task(task_id="t", intent="i", session_scope="task-42")

    assert [factory_calls[name]["session_scope"] for name in HEAD_FACTORIES] == [
        "task-42"
    ] * len(HEAD_FACTORIES)


async def test_run_task_leaves_heads_unscoped_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls: dict[str, dict[str, Any]] = {}

    async def _fake_run_task(**kwargs: Any) -> Any:
        return _minimal_result()

    def _sentinel(name: str) -> Any:
        def factory(harness: Any, **kw: Any) -> str:
            factory_calls[name] = kw
            return f"sentinel-{name}"

        return factory

    monkeypatch.setattr("dream.runner.task.run_task", _fake_run_task)
    for name in HEAD_FACTORIES:
        monkeypatch.setattr(f"dream.runner.{name}", _sentinel(name))

    harness = Harness(HarnessConfig(working_dir=Path("/wt")))
    await harness.run_task(task_id="t", intent="i")

    assert all(factory_calls[name]["session_scope"] is None for name in HEAD_FACTORIES)


async def test_generator_head_runs_in_the_scoped_generator_thread() -> None:
    stub = _RecordingHarness()
    head = make_generator_head(_as_harness(stub), session_scope="task-42")

    await head("task-42", 1, None, LedgerStep(id="s1", description="do thing"))

    assert stub.calls == [("generator", "task-42-generator")]


async def test_generator_head_stays_unnamed_without_a_scope() -> None:
    stub = _RecordingHarness()
    head = make_generator_head(_as_harness(stub))

    await head("task-42", 1, None, LedgerStep(id="s1", description="do thing"))

    assert stub.calls == [("generator", None)]


def _minimal_result() -> Any:
    from tests.test_harness_run_task import _make_minimal_run_task_result

    return _make_minimal_run_task_result()
