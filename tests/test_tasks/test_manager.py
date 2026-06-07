"""Spec 07 slice 2 — :class:`BackgroundTaskManager` runtime.

The manager owns the ephemeral side of the task engine: spawning a
subprocess (shell-string or argv), watching it to terminal state,
streaming its output to a per-task ``output_file``, and firing
**completion listeners** that other layers (e.g. the durable ledger
seam) hook into. All state is in-memory; nothing on disk except each
task's ``output_file`` (#13 §"Runtime task lifecycle (ephemeral)").

These tests pin the spec's runtime acceptance criteria:

- ``pending -> running -> {completed, failed, killed}`` transitions
  (``test_task_record_status_transitions_*``),
- ``return_code`` capture on natural exit
  (``test_task_return_code_recorded_*``),
- completion listeners fire on both natural exit and ``stop_task``
  (``test_completion_listener_fires_on_*``),
- ``output_file`` accumulates incrementally
  (``test_task_output_streams_incrementally``),
- the ``argv`` launch form bypasses the shell entirely
  (``test_argv_launch_bypasses_shell``),
- a restarted agent task bumps ``_generations`` and writes a restart
  notice (``test_restart_bumps_generation``).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._types import TaskRecord

# --- helpers ---------------------------------------------------------------


async def _wait_until_done(
    manager: BackgroundTaskManager,
    task_id: str,
    *,
    timeout: float = 10.0,
) -> TaskRecord:
    """Poll the task until it reaches a terminal status."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        task = manager.get_task(task_id)
        assert task is not None
        if task.status in {"completed", "failed", "killed"}:
            return task
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"task {task_id} did not finish; status={task.status}")
        await asyncio.sleep(0.05)


def _py_argv(code: str) -> list[str]:
    """Run a tiny Python program directly (argv form, no shell)."""
    return [sys.executable, "-c", code]


# --- creation contract -----------------------------------------------------


async def test_create_shell_task_requires_command_or_argv(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    with pytest.raises(ValueError, match="command or argv"):
        await mgr.create_shell_task(description="empty", cwd=tmp_path)


async def test_create_shell_task_rejects_both(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    with pytest.raises(ValueError, match="only one of"):
        await mgr.create_shell_task(
            description="both",
            cwd=tmp_path,
            command="echo hi",
            argv=["echo", "hi"],
        )


async def test_create_shell_task_via_argv_sets_record_fields(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    record = await mgr.create_shell_task(
        description="hello",
        cwd=tmp_path,
        argv=_py_argv("print('ok')"),
    )
    assert record.id.startswith("local_bash-")
    assert record.type == "local_bash"
    assert record.status == "running"
    assert record.cwd == str(tmp_path.resolve())
    assert record.argv == tuple(_py_argv("print('ok')"))
    assert record.command is None
    assert record.output_file.parent == tmp_path
    assert record.output_file.exists()  # opened eagerly
    await _wait_until_done(mgr, record.id)


# --- lifecycle: natural exit ----------------------------------------------


async def test_task_return_code_recorded_on_success(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    record = await mgr.create_shell_task(
        description="ok",
        cwd=tmp_path,
        argv=_py_argv("import sys; sys.exit(0)"),
    )
    final = await _wait_until_done(mgr, record.id)
    assert final.status == "completed"
    assert final.return_code == 0
    assert final.ended_at is not None


async def test_task_return_code_recorded_on_failure(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    record = await mgr.create_shell_task(
        description="boom",
        cwd=tmp_path,
        argv=_py_argv("import sys; sys.exit(7)"),
    )
    final = await _wait_until_done(mgr, record.id)
    assert final.status == "failed"
    assert final.return_code == 7


# --- completion listeners --------------------------------------------------


async def test_completion_listener_fires_on_natural_exit(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    seen: list[TaskRecord] = []

    async def listener(task: TaskRecord) -> None:
        seen.append(task)

    mgr.register_completion_listener(listener)
    record = await mgr.create_shell_task(
        description="natural",
        cwd=tmp_path,
        argv=_py_argv("print('done')"),
    )
    await _wait_until_done(mgr, record.id)
    # listener is awaited inside the watcher; one more loop tick to flush
    await asyncio.sleep(0.05)
    assert len(seen) == 1
    assert seen[0].id == record.id
    assert seen[0].status == "completed"


async def test_completion_listener_fires_on_stop(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    seen: list[TaskRecord] = []

    def listener(task: TaskRecord) -> None:  # sync listener — must also be supported
        seen.append(task)

    mgr.register_completion_listener(listener)
    record = await mgr.create_shell_task(
        description="forever",
        cwd=tmp_path,
        argv=_py_argv("import time; time.sleep(30)"),
    )
    await asyncio.sleep(0.2)  # let it actually start
    final = await mgr.stop_task(record.id)
    assert final.status == "killed"
    assert len(seen) == 1
    assert seen[0].status == "killed"


async def test_completion_listener_unregister(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    seen: list[TaskRecord] = []
    unregister = mgr.register_completion_listener(lambda t: seen.append(t))
    unregister()
    record = await mgr.create_shell_task(
        description="x",
        cwd=tmp_path,
        argv=_py_argv("pass"),
    )
    await _wait_until_done(mgr, record.id)
    await asyncio.sleep(0.05)
    assert seen == []


# --- output streaming ------------------------------------------------------


async def test_task_output_streams_incrementally(tmp_path: Path) -> None:
    """The output_file should accumulate before the task exits."""
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    record = await mgr.create_shell_task(
        description="trickle",
        cwd=tmp_path,
        argv=_py_argv(
            "import sys, time;"
            "print('first', flush=True); time.sleep(0.4);"
            "print('second', flush=True)"
        ),
    )
    # mid-flight read: should see 'first' but not 'second'
    await asyncio.sleep(0.2)
    mid = mgr.read_task_output(record.id)
    assert "first" in mid
    assert "second" not in mid
    # after exit: both
    await _wait_until_done(mgr, record.id)
    final = mgr.read_task_output(record.id)
    assert "first" in final
    assert "second" in final


# --- argv-vs-command -------------------------------------------------------


async def test_argv_launch_bypasses_shell(tmp_path: Path) -> None:
    """argv form must NOT go through a shell — metacharacters stay literal."""
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    # If a shell were involved, `;` and `$(...)` would be interpreted. With
    # argv exec the whole string is one positional argument.
    suspicious = "hello; echo PWNED $(whoami)"
    record = await mgr.create_shell_task(
        description="argv literal",
        cwd=tmp_path,
        argv=[sys.executable, "-c", "import sys; print(sys.argv[1])", suspicious],
    )
    await _wait_until_done(mgr, record.id)
    out = mgr.read_task_output(record.id)
    assert suspicious in out
    assert "PWNED" not in out.replace(suspicious, "")  # PWNED only inside the echoed literal


# --- restart ---------------------------------------------------------------


async def test_restart_bumps_generation(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    record = await mgr.create_shell_task(
        description="restartable",
        cwd=tmp_path,
        argv=_py_argv("import time; time.sleep(30)"),
        task_type="local_agent",
    )
    assert mgr.generation(record.id) == 1
    await asyncio.sleep(0.15)
    await mgr.restart_task(record.id)
    assert mgr.generation(record.id) == 2
    # the restart notice should appear in the output file
    await asyncio.sleep(0.1)
    out = mgr.read_task_output(record.id)
    assert "restarted" in out.lower()
    await mgr.stop_task(record.id)


async def test_restart_clears_terminal_fields(tmp_path: Path) -> None:
    """A restart must rebind to a fresh run: clear ``ended_at`` /
    ``return_code`` and stamp a new ``started_at`` (#62). Otherwise the
    restarted task still looks finished to observers."""
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    record = await mgr.create_shell_task(
        description="agent",
        cwd=tmp_path,
        argv=_py_argv("import sys; sys.exit(0)"),
        task_type="local_agent",
    )
    # Let it exit naturally so terminal fields are populated.
    done = await _wait_until_done(mgr, record.id)
    assert done.ended_at is not None
    assert done.return_code is not None
    old_started = done.started_at

    await mgr.restart_task(record.id)
    after = mgr.get_task(record.id)
    assert after is not None
    assert after.status == "running"
    assert after.ended_at is None
    assert after.return_code is None
    assert after.started_at is not None
    assert after.started_at != old_started
    await mgr.stop_task(record.id)


async def test_stop_task_clears_suppression_on_process_lookup_error(
    tmp_path: Path,
) -> None:
    """If ``terminate()`` raises ``ProcessLookupError`` (child already gone),
    the watcher-suppression set must still be cleared in ``finally`` (#61).
    Otherwise the id is wedged and later notifications never fire."""
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    record = await mgr.create_shell_task(
        description="boom",
        cwd=tmp_path,
        argv=_py_argv("import time; time.sleep(30)"),
    )
    await asyncio.sleep(0.1)

    process = mgr._processes[record.id]

    def _raise() -> None:
        raise ProcessLookupError("already reaped")

    # Make terminate raise; the real process is still killed via process.kill
    # in the timeout path, but suppression must clear regardless.
    process.terminate = _raise  # type: ignore[method-assign]

    await mgr.stop_task(record.id)
    assert record.id not in mgr._suppress_watcher_notify


async def test_create_shell_task_no_ghost_on_spawn_failure(tmp_path: Path) -> None:
    """A spawn failure must not leave a ghost task in ``running`` state with
    no process behind it (#60)."""
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    # A non-existent executable makes create_subprocess_exec raise.
    with pytest.raises((FileNotFoundError, OSError)):
        await mgr.create_shell_task(
            description="bad",
            cwd=tmp_path,
            argv=["/nonexistent/definitely-not-here-xyz"],
        )
    # No task, lock, or generation should linger.
    assert mgr.list_tasks() == []
    assert mgr._output_locks == {}
    assert mgr._generations == {}


async def test_awaitable_non_coroutine_listener_is_awaited(tmp_path: Path) -> None:
    """Listeners returning a non-coroutine awaitable (e.g. a Future) must be
    awaited, not silently dropped (#63)."""
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    awaited = asyncio.Event()

    class _Awaitable:
        def __await__(self):  # type: ignore[no-untyped-def]
            awaited.set()
            yield from asyncio.sleep(0).__await__()

    def listener(task: TaskRecord) -> _Awaitable:
        return _Awaitable()

    mgr.register_completion_listener(listener)
    record = await mgr.create_shell_task(
        description="x",
        cwd=tmp_path,
        argv=_py_argv("pass"),
    )
    await _wait_until_done(mgr, record.id)
    await asyncio.sleep(0.1)
    assert awaited.is_set()


async def test_restart_rejected_for_shell_task(tmp_path: Path) -> None:
    """Only agent-like task types can be restarted (#13 §restart semantics)."""
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    record = await mgr.create_shell_task(
        description="bash",
        cwd=tmp_path,
        argv=_py_argv("import time; time.sleep(30)"),
        task_type="local_bash",
    )
    await asyncio.sleep(0.1)
    with pytest.raises(ValueError, match="cannot be restarted"):
        await mgr.restart_task(record.id)
    await mgr.stop_task(record.id)


# --- listing & lookup ------------------------------------------------------


async def test_get_task_unknown_returns_none(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    assert mgr.get_task("nope") is None


async def test_stop_task_unknown_raises(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    with pytest.raises(ValueError, match="No task"):
        await mgr.stop_task("nope")


async def test_list_tasks_filters_by_status(tmp_path: Path) -> None:
    mgr = BackgroundTaskManager(tasks_dir=tmp_path)
    quick = await mgr.create_shell_task(
        description="quick",
        cwd=tmp_path,
        argv=_py_argv("pass"),
    )
    slow = await mgr.create_shell_task(
        description="slow",
        cwd=tmp_path,
        argv=_py_argv("import time; time.sleep(30)"),
    )
    await _wait_until_done(mgr, quick.id)
    await asyncio.sleep(0.05)

    completed = mgr.list_tasks(status="completed")
    running = mgr.list_tasks(status="running")
    assert [t.id for t in completed] == [quick.id]
    assert [t.id for t in running] == [slow.id]

    await mgr.stop_task(slow.id)
