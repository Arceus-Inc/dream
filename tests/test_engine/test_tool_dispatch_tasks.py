"""Spec 07 wiring — engine ``EngineToolDispatcher`` ↔ task tools.

Pin the seam end-to-end without bringing up an LLM:

1. ``TaskSessionContext`` is reachable through ``ctx.metadata`` from a
   tool's ``execute``.
2. ``task_create`` actually spawns a tracked task in the supplied
   ``BackgroundTaskManager``.
3. The ``task_id`` emitted by ``task_create`` round-trips through
   ``task_get``, so the same manager is observed by both tools.
"""

from __future__ import annotations

import json
from pathlib import Path

from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.tasks import (
    TASK_CONTEXT_KEY,
    BackgroundTaskManager,
    TaskSessionContext,
)
from dream.tools.builtin import default_registry, register_task_tools


def _task_registry():
    registry = default_registry()
    register_task_tools(registry)
    return registry


async def test_task_create_then_get_round_trips_through_dispatcher(tmp_path: Path) -> None:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    session = TaskSessionContext(manager=manager)
    dispatcher = EngineToolDispatcher(
        registry=_task_registry(),
        working_dir=tmp_path,
        session_id="s_int",
        context_metadata={TASK_CONTEXT_KEY: session},
    )

    # 1) task_create spawns a real task in the wired manager.
    create_content, create_err = await dispatcher.dispatch(
        "task_create",
        {
            "description": "echo from integration test",
            "command": "python -c \"print('hi')\"",
        },
    )
    assert create_err is False, create_content
    # The dispatcher returns content as either a string or a JSON-wrapped
    # tool envelope depending on size; just assert the human-facing summary
    # mentions the description and pull the task id from the manager.
    assert "echo from integration test" in create_content
    tasks = manager.list_tasks()
    assert len(tasks) == 1
    task_id = tasks[0].id

    # 2) The same id is observable through task_get without re-wiring.
    get_content, get_err = await dispatcher.dispatch("task_get", {"task_id": task_id})
    assert get_err is False
    assert task_id in get_content
    assert "local_bash" in get_content


async def test_task_create_missing_context_is_structured_error(tmp_path: Path) -> None:
    dispatcher = EngineToolDispatcher(
        registry=_task_registry(),
        working_dir=tmp_path,
        session_id="s_no_ctx",
    )
    content, is_error = await dispatcher.dispatch(
        "task_create", {"description": "d", "command": "true"}
    )
    assert is_error is True
    # Structured error envelopes carry root_cause in metadata; the dispatcher
    # collapses to a JSON-ish string for the engine. We just need the
    # human-readable lead to mention that task tools aren't wired.
    assert "background task" in content.lower() or "session context" in content.lower()
    # And the json hint surface should be parseable when present.
    if content.lstrip().startswith("{"):
        envelope = json.loads(content)
        assert "root_cause" in envelope
