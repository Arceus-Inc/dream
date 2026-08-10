"""Spec 10-I — :class:`StdioObserver` formats run_task progress.

The observer is the operator-visible side of ``run_task`` / ``run_role``.
Tests pin the *shape* of each line so external tooling (and humans
reading the terminal) can rely on stable prefixes (e.g. ``[planner]``,
``[sprint 1]``, ``[generator] tool→`` / ``tool←``).
"""

from __future__ import annotations

import io

from dream.engine._cost import UsageSnapshot
from dream.runner.events import (
    ContractWritten,
    EvaluatorCompleted,
    EvaluatorStarted,
    GeneratorCompleted,
    GeneratorStarted,
    HeadRetry,
    PlannerCompleted,
    PlannerSkipped,
    PlannerStarted,
    RoleError,
    RoleSessionClosed,
    RoleSessionOpened,
    RoleText,
    RoleToolResult,
    RoleToolStart,
    SprintCompleted,
    SprintEscalated,
    SprintStarted,
    TaskCompleted,
    TaskStarted,
)
from dream.runner.observe import CapturingObserver, StdioObserver


def test_capturing_observer_records_events_in_order() -> None:
    obs = CapturingObserver()
    obs.on_event(TaskStarted(task_id="t1", intent="hi"))
    obs.on_event(TaskCompleted(task_id="t1", sprint_count=0))

    assert [e.kind for e in obs.events] == ["task.started", "task.completed"]


def test_stdio_observer_writes_task_lifecycle_lines() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf)

    obs.on_event(TaskStarted(task_id="t-001", intent="ship things"))
    obs.on_event(TaskCompleted(task_id="t-001", sprint_count=3))

    out = buf.getvalue()
    assert "[task] start" in out
    assert "task_id='t-001'" in out
    assert "intent='ship things'" in out
    assert "[task] done" in out
    assert "sprints=3" in out


def test_stdio_observer_writes_planner_lines() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf)

    obs.on_event(PlannerStarted(task_id="t-1"))
    obs.on_event(
        PlannerCompleted(
            task_id="t-1",
            spec_path="docs/exec-plans/active/t-1.md",
            ledger_path="docs/exec-plans/active/t-1.json",
            step_count=4,
        )
    )

    out = buf.getvalue()
    assert "[planner] drafting spec" in out
    assert "[planner] done" in out
    assert "docs/exec-plans/active/t-1.md" in out
    assert "4 steps" in out


def test_stdio_observer_writes_sprint_and_contract_lines() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf)

    obs.on_event(
        SprintStarted(
            sprint_number=1,
            step_id="s1",
            step_description="Wire the thing",
        )
    )
    obs.on_event(
        ContractWritten(
            sprint_number=1,
            path="docs/exec-plans/active/t-1-sprint-1.json",
        )
    )
    obs.on_event(
        SprintCompleted(
            sprint_number=1,
            step_id="s1",
            outcome="pass",
        )
    )

    out = buf.getvalue()
    assert "[sprint 1] start step='s1'" in out
    assert "Wire the thing" in out
    assert "contract written" in out
    assert "[sprint 1] done step='s1' outcome=pass" in out


def test_stdio_observer_writes_generator_and_evaluator_lines() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf)

    obs.on_event(
        GeneratorStarted(
            sprint_number=2,
            step_id="s2",
            has_contract=True,
        )
    )
    obs.on_event(GeneratorCompleted(sprint_number=2, step_id="s2"))
    obs.on_event(EvaluatorStarted(sprint_number=2, step_id="s2"))
    obs.on_event(
        EvaluatorCompleted(
            sprint_number=2,
            outcome="needs-changes",
            score=0.6,
            notes="fix the tests",
        )
    )

    out = buf.getvalue()
    assert "[sprint 2] generator start" in out
    assert "with contract" in out
    assert "[sprint 2] generator done" in out
    assert "[sprint 2] evaluator start" in out
    assert "outcome='needs-changes'" in out
    assert "score=0.6" in out
    assert "fix the tests" in out


def test_stdio_observer_marks_generator_without_contract() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf)

    obs.on_event(
        GeneratorStarted(
            sprint_number=1,
            step_id="s",
            has_contract=False,
        )
    )

    assert "no contract (evaluator off)" in buf.getvalue()


def test_stdio_observer_writes_role_session_and_tool_lines() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf, role_text_buffering=False)

    obs.on_event(
        RoleSessionOpened(
            role="generator",
            session_id="sess-1",
        )
    )
    obs.on_event(
        RoleToolStart(
            role="generator",
            tool="write_file",
            input={"path": "src/x.py", "content": "..."},
        )
    )
    obs.on_event(
        RoleToolResult(
            role="generator",
            tool="write_file",
            is_error=False,
            content="wrote 42 bytes",
            content_preview="wrote 42 bytes",
        )
    )
    obs.on_event(
        RoleSessionClosed(
            role="generator",
            session_id="sess-1",
            model="m",
            usage=UsageSnapshot(),
            cost_usd=0.0012,
        )
    )

    out = buf.getvalue()
    assert "[generator] session open" in out
    assert "[generator] tool\u2192 write_file" in out  # tool→
    assert "[generator] tool\u2190 write_file [ok]" in out  # tool←
    assert "wrote 42 bytes" in out
    assert "[generator] session close" in out
    assert "cost_usd=0.0012" in out


def test_stdio_observer_buffers_role_text_until_session_close_by_default() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf)  # buffering = True

    obs.on_event(RoleSessionOpened(role="planner", session_id="s"))
    obs.on_event(RoleText(role="planner", text="Hello, "))
    obs.on_event(RoleText(role="planner", text="world."))

    # Mid-stream nothing is printed for role.text:
    mid = buf.getvalue()
    assert "Hello" not in mid

    obs.on_event(
        RoleSessionClosed(
            role="planner",
            session_id="s",
            model="m",
            usage=UsageSnapshot(),
            cost_usd=0.0,
        )
    )

    out = buf.getvalue()
    assert "[planner] reply:" in out
    assert "Hello, world." in out


def test_stdio_observer_streams_role_text_when_buffering_disabled() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf, role_text_buffering=False)

    obs.on_event(RoleText(role="generator", text="chunk1 "))
    obs.on_event(RoleText(role="generator", text="chunk2"))

    out = buf.getvalue()
    assert "[generator] chunk1" in out
    assert "[generator] chunk2" in out


def test_stdio_observer_writes_role_error_line() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf)

    obs.on_event(RoleError(role="evaluator", message="boom"))

    assert "[evaluator] ERROR 'boom'" in buf.getvalue()


def test_stdio_observer_formats_head_retry_planner_skipped_sprint_escalated() -> None:
    buf = io.StringIO()
    obs = StdioObserver(stream=buf)

    obs.on_event(
        HeadRetry(
            role="planner",
            attempt=1,
            error="bad json",
        )
    )
    obs.on_event(
        PlannerSkipped(
            task_id="t1",
            reason="ledger already present",
        )
    )
    obs.on_event(
        SprintEscalated(
            task_id="t1",
            sprint_number=2,
            step_id="s1",
            needs_changes_count=2,
            strikes_this_run=2,
            reason="strike limit",
        )
    )

    out = buf.getvalue()
    assert "[planner] retry#1" in out
    assert "bad json" in out
    assert "[planner] skipped" in out
    assert "ledger already present" in out
    assert "[sprint 2] escalated" in out
    assert "step=s1" in out
