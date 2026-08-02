"""Default ``spawn_subagent`` tool — Cursor-style type enum + Hermes child isolation.

spawn_subagent(subagent_type, goal, context?) -> SubagentResult
  # subagent_type — enum: generalPurpose + SubagentSet names (fail-closed)
  # goal — bounded task (alias: prompt); context — packed inlet, not parent history

``name`` is accepted as an alias for ``subagent_type`` for one release.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, model_validator

from dream.contracts.hook import SubagentJoinMode
from dream.contracts.tool import ToolResult
from dream.observability._tracer import Tracer
from dream.subagents._declaration import Subagent, SubagentSet
from dream.subagents._projection import SubagentResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

if TYPE_CHECKING:
    from dream.harness import Harness

SUBAGENT_SET_CONTEXT_KEY = "dream.subagent_set"
OBSERVER_KEY = "dream.observer"
PARENT_SESSION_KEY = "dream.parent_session_id"
PARENT_TOOLS_KEY = "dream.parent_tools"
PARENT_PERMISSIONS_KEY = "dream.parent_permissions"
TEAM_KEY = "dream.team"
TRACER_KEY = "dream.tracer"
HARNESS_KEY = "dream.harness"
SPAWN_COUNT_KEY = "dream.subagent_spawn_count"
SPAWN_LEDGER_KEY = "dream.subagent_spawn_ledger"

MAX_SPAWNS_PER_BEAT = 10
MAX_BATCH_TASKS = 3
SPAWN_SUBAGENT_TOOL = "spawn_subagent"


class SpawnJoinMode(StrEnum):
    DELEGATE = "delegate"


class SpawnDispatchStatus(StrEnum):
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"


GENERAL_PURPOSE = "generalPurpose"

_DEFAULT_GP_TOOLS = (
    "read_file",
    "grep",
    "glob",
    "bash",
    "write_file",
    "edit_file",
)


def spawn_type_names(subagent_set: SubagentSet | None) -> list[str]:
    """Enum values for this beat: generalPurpose first, then Spec names."""
    names = list(subagent_set.names()) if subagent_set else []
    return [GENERAL_PURPOSE, *names]


def spawn_label_from_input(tool_input: Mapping[str, Any]) -> str:
    """Resolved spawn type/name from tool args (post-hook replacement)."""
    return str(tool_input.get("subagent_type") or tool_input.get("name") or "").strip()


def resolve_spawn_goal(goal: str | None, prompt: str | None) -> str:
    """Prefer non-blank goal; fall back to legacy prompt."""
    return (goal or "").strip() or (prompt or "").strip()


def unknown_subagent_result(type_name: str, available: list[str]) -> ToolResult:
    """Fail-closed when subagent_type is not in the beat enum."""
    return ToolResult(
        content=f"Subagent {type_name!r} not found. Available subagents: {available}",
        is_error=True,
        metadata={
            "root_cause": f"unknown_subagent: {type_name}",
            "safe_retry": f"Use one of: {available}",
            "stop_condition": "The requested subagent does not exist.",
        },
    )


def general_purpose_agent(parent_tools: frozenset[str] | None) -> Subagent:
    """Hermes leaf worker — parent tools minus spawn (no nesting)."""
    tools: tuple[str, ...]
    if parent_tools is None:
        tools = _DEFAULT_GP_TOOLS
    else:
        tools = tuple(t for t in sorted(parent_tools) if t != "spawn_subagent")
    return Subagent(
        name=GENERAL_PURPOSE,
        description=(
            "Ad-hoc delegated worker. Fresh context; returns a summary. "
            "Use for reasoning-heavy subtasks that would flood the parent."
        ),
        tools=tools,
        max_turns=8,
    )


def build_spawn_parameters(
    base_schema: dict[str, Any],
    subagent_set: SubagentSet | None,
) -> dict[str, Any]:
    """Patch JSON schema with a dynamic subagent_type enum + short choice matrix."""
    types = spawn_type_names(subagent_set)
    catalog_lines = [
        f"- {GENERAL_PURPOSE}: ad-hoc fresh-context worker (summary only; no evidence gate)"
    ]
    if subagent_set:
        for name, desc in subagent_set.descriptions().items():
            short = desc.split("\n", 1)[0].strip()
            if len(short) > 120:
                short = short[:117] + "..."
            catalog_lines.append(f"- {name}: {short}")
    catalog = "\n".join(catalog_lines)

    schema = dict(base_schema)
    props = {
        k: dict(v) if isinstance(v, dict) else v
        for k, v in (schema.get("properties") or {}).items()
    }
    props["subagent_type"] = {
        "type": "string",
        "enum": types,
        "description": (
            "Which agent template to launch.\n\n"
            f"{catalog}\n\n"
            "WHEN TO USE: reasoning-heavy subtask, fresh/unbiased eyes, or a required specialist.\n"
            "WHEN NOT: single tool call — call it yourself. "
            "Evidence specialists must use their exact subagent_type (never forge their artifacts)."
        ),
    }
    # Prefer subagent_type; keep name for alias validation without requiring both.
    required = [r for r in (schema.get("required") or []) if r not in ("name", "subagent_type")]
    schema["properties"] = props
    schema["required"] = required
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        task_definition = definitions.get("SpawnTaskInput")
        if isinstance(task_definition, dict):
            task_properties = task_definition.get("properties")
            if isinstance(task_properties, dict):
                task_properties["subagent_type"] = {
                    "type": "string",
                    "enum": types,
                    "description": "Agent template for this fan-out task.",
                }
    return schema


class SpawnTaskInput(BaseModel):
    """One bounded child in a fan-out request."""

    subagent_type: str
    goal: str = Field(min_length=1)
    context: str | None = None


class SpawnSubagentInput(BaseModel):
    """Arguments for ``spawn_subagent``."""

    subagent_type: str | None = Field(
        default=None,
        description="Agent template to launch (enum: generalPurpose + role specialists).",
    )
    name: str | None = Field(
        default=None,
        description="Deprecated alias for subagent_type.",
    )
    prompt: str = Field(
        default="",
        description="Legacy task text. Prefer goal.",
    )
    goal: str | None = Field(
        default=None,
        description="Delegated task for the child (preferred).",
    )
    context: str | None = Field(
        default=None,
        description="Packed extras for the child — not parent history.",
    )
    background: bool = Field(
        default=False,
        description=(
            "If true, prefer keep-working join. When the async drain rail is unavailable, "
            "forced sync with a note."
        ),
    )
    tasks: tuple[SpawnTaskInput, ...] | None = Field(
        default=None,
        description=f"Concurrent fan-out of 1-{MAX_BATCH_TASKS} bounded tasks.",
    )

    @model_validator(mode="after")
    def validate_shape(self) -> SpawnSubagentInput:
        has_single = bool((self.subagent_type or self.name or "").strip())
        has_tasks = self.tasks is not None
        if has_single == has_tasks:
            raise ValueError("pass exactly one of subagent_type/name or tasks")
        if has_single and not resolve_spawn_goal(self.goal, self.prompt):
            raise ValueError("single spawn requires goal or prompt")
        if self.tasks is not None and not 1 <= len(self.tasks) <= MAX_BATCH_TASKS:
            raise ValueError(f"tasks must contain 1 to {MAX_BATCH_TASKS} items")
        return self


@dataclass
class SpawnLedger:
    """Per-session machine cap: each specialist name may be claimed once."""

    names: set[str] = field(default_factory=set)

    def claim(self, requested: tuple[str, ...]) -> str | None:
        duplicate = next((name for name in requested if name in self.names), None)
        if duplicate is not None:
            return duplicate
        if len(set(requested)) != len(requested):
            return next(name for name in requested if requested.count(name) > 1)
        self.names.update(requested)
        return None


@dataclass(frozen=True)
class _ResolvedTask:
    agent: Subagent
    goal: str
    context: str | None


class SpawnTaskOutput(BaseModel):
    subagent_type: str
    status: SpawnDispatchStatus
    output: str = ""
    error: str | None = None
    turns_used: int = 0
    tool_calls: int = 0
    tool_errors: int = 0


class SpawnBatchOutput(BaseModel):
    status: SpawnDispatchStatus
    mode: SubagentJoinMode
    results: tuple[SpawnTaskOutput, ...]
    elapsed_seconds: float
    artifacts: tuple[str, ...] = ()


class SpawnBackgroundOutput(BaseModel):
    status: SpawnDispatchStatus
    mode: SubagentJoinMode = SubagentJoinMode.BACKGROUND
    delegation_id: str
    count: int
    subagent_types: tuple[str, ...]
    artifacts: tuple[str, ...] = ()
    note: str = "Completion will arrive as a new user turn; keep working now."


class SpawnSubagentTool(BaseTool):
    """Dispatch a subagent to do bounded work and return its result."""

    name = SPAWN_SUBAGENT_TOOL
    description = (
        "Spawn a focused subagent in an isolated context. "
        "Pick subagent_type from the enum (generalPurpose or a role specialist). "
        "Pass a self-contained goal (+ optional context). "
        "Parent sees only the summary — not the child's intermediate tool I/O. "
        "Use specialists when evidence/contracts require them; use generalPurpose for ad-hoc work."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=300.0)
    input_model = SpawnSubagentInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        from dream.subagents._delegate import run_subagent_delegate

        args = SpawnSubagentInput.model_validate(input)
        subagent_set: SubagentSet | None = ctx.metadata.get(SUBAGENT_SET_CONTEXT_KEY)
        available = spawn_type_names(subagent_set)
        parent_tools: frozenset[str] | None = ctx.metadata.get(PARENT_TOOLS_KEY)
        requested = (
            args.tasks
            if args.tasks is not None
            else (
                SpawnTaskInput(
                    subagent_type=(args.subagent_type or args.name or "").strip(),
                    goal=resolve_spawn_goal(args.goal, args.prompt),
                    context=args.context,
                ),
            )
        )
        resolved: list[_ResolvedTask] = []
        for task in requested:
            type_name = task.subagent_type.strip()
            agent: Subagent | None
            if type_name == GENERAL_PURPOSE:
                agent = general_purpose_agent(parent_tools)
            else:
                agent = subagent_set.get(type_name) if subagent_set is not None else None
            if agent is None:
                return unknown_subagent_result(type_name, available)
            resolved.append(
                _ResolvedTask(agent=agent, goal=task.goal.strip(), context=task.context)
            )

        counter: list[int] | None = ctx.metadata.get(SPAWN_COUNT_KEY)
        if counter is None:
            counter = [0]
            ctx.metadata[SPAWN_COUNT_KEY] = counter
        counter[0] += len(resolved)
        if counter[0] > MAX_SPAWNS_PER_BEAT:
            return ToolResult(
                content=(
                    f"Spawn cap exceeded ({MAX_SPAWNS_PER_BEAT} per beat). "
                    f"Complete remaining work yourself."
                ),
                is_error=True,
                metadata={
                    "root_cause": "spawn_cap_exceeded",
                    "safe_retry": "Do the work directly instead of delegating.",
                    "stop_condition": f"Max {MAX_SPAWNS_PER_BEAT} subagent spawns per beat.",
                },
            )
        harness: Harness | None = ctx.metadata.get(HARNESS_KEY)
        if harness is None:
            return ToolResult(
                content="No harness wired for subagent execution.",
                is_error=True,
                metadata={
                    "root_cause": "no_harness",
                    "safe_retry": "Subagent execution requires a wired harness.",
                    "stop_condition": "Cannot spawn subagents without a harness.",
                },
            )
        ledger: SpawnLedger | None = ctx.metadata.get(SPAWN_LEDGER_KEY)
        if ledger is None:
            ledger = SpawnLedger()
            ctx.metadata[SPAWN_LEDGER_KEY] = ledger
        names = tuple(task.agent.name for task in resolved)
        # generalPurpose is repeatable ad-hoc work; evidence specialists remain one-shot.
        ledger_names = tuple(name for name in names if name != GENERAL_PURPOSE)
        duplicate = ledger.claim(ledger_names)
        if duplicate is not None:
            return ToolResult(
                content=f"Subagent {duplicate!r} already spawned in this session.",
                is_error=True,
                metadata={
                    "root_cause": f"subagent_already_spawned: {duplicate}",
                    "safe_retry": "Use the existing result or complete the work directly.",
                    "stop_condition": "Each subagent name may be spawned once per session.",
                },
            )
        tracer: Tracer | None = ctx.metadata.get(TRACER_KEY)
        dispatch_started = time.monotonic()

        async def run_one(task: _ResolvedTask) -> SubagentResult:
            spawn_time = time.monotonic()
            agent = task.agent
            if tracer is not None:
                tracer.event(
                    "subagent.spawn",
                    {
                        "subagent_name": agent.name,
                        "subagent_type": agent.name,
                        "prompt": task.goal,
                        "depth": agent.depth,
                        "tools": list(agent.tools),
                        "model": agent.model or "parent_model",
                        "parent_session_id": ctx.session_id,
                    },
                )
            result = await run_subagent_delegate(
                agent,
                goal=task.goal,
                context=task.context,
                harness=harness,
                parent_tools=parent_tools,
                spawn_counter=counter,
                tracer=tracer,
                observer=ctx.metadata.get(OBSERVER_KEY),
                working_dir=ctx.working_dir,
                spill_dir=ctx.scratch_dir,
            )
            if tracer is not None:
                tracer.event(
                    "subagent.complete",
                    {
                        "subagent_name": agent.name,
                        "subagent_type": agent.name,
                        "success": result.success,
                        "turns_used": result.turns_used,
                        "tool_calls": result.tool_calls,
                        "tool_errors": result.tool_errors,
                        "elapsed_seconds": round(time.monotonic() - spawn_time, 2),
                        "error": result.error,
                    },
                )
            return result

        async def run_all() -> tuple[SubagentResult, ...]:
            return tuple(await asyncio.gather(*(run_one(task) for task in resolved)))

        background_supported = (
            args.background
            and ctx.delegations is not None
        )
        forced_sync_note = ""
        if background_supported:
            assert ctx.delegations is not None
            handle = ctx.delegations.start(ctx.session_id, names, run_all)
            if handle is not None:
                background_response = SpawnBackgroundOutput(
                    status=SpawnDispatchStatus.DISPATCHED,
                    delegation_id=handle.delegation_id,
                    count=len(resolved),
                    subagent_types=names,
                )
                return ToolResult(
                    content=background_response.model_dump_json(),
                    structured=background_response.model_dump(),
                    metadata={
                        "summary": f"dispatched {len(resolved)} background subagent(s)",
                        "mode": SubagentJoinMode.BACKGROUND.value,
                        "delegation_id": handle.delegation_id,
                        "subagent_name": names[0] if len(names) == 1 else "batch",
                    },
                )
            forced_sync_note = "capacity unavailable"
        elif args.background:
            forced_sync_note = (
                "delivery unavailable"
                if ctx.delegations is None
                else "synchronous delegation requires parent join"
            )

        results = await run_all()
        if len(results) == 1:
            result = results[0]
            if not result.success:
                failure_response = SpawnBatchOutput(
                    status=SpawnDispatchStatus.FAILED,
                    mode=SubagentJoinMode.SYNC,
                    results=(
                        SpawnTaskOutput(
                            subagent_type=result.name,
                            status=SpawnDispatchStatus.FAILED,
                            error=result.error,
                        ),
                    ),
                    elapsed_seconds=round(time.monotonic() - dispatch_started, 2),
                )
                return ToolResult(
                    content=f"Subagent {result.name!r} failed: {result.error}",
                    structured=failure_response.model_dump(),
                    is_error=True,
                    metadata={
                        "root_cause": f"subagent_failed: {result.error}",
                        "safe_retry": "Try rephrasing the task or do it yourself.",
                        "stop_condition": "The subagent could not complete the task.",
                        "subagent_name": result.name,
                        "mode": SubagentJoinMode.SYNC.value,
                    },
                )
            content = result.output
            if result.warning:
                content = f"{result.warning}\n\n{content}"
            if forced_sync_note:
                content += f"\n\n[note: background=true forced sync — {forced_sync_note}.]"
            success_response = SpawnBatchOutput(
                status=SpawnDispatchStatus.COMPLETED,
                mode=SubagentJoinMode.SYNC,
                results=(
                    SpawnTaskOutput(
                        subagent_type=result.name,
                        status=SpawnDispatchStatus.COMPLETED,
                        output=result.output,
                        turns_used=result.turns_used,
                        tool_calls=result.tool_calls,
                        tool_errors=result.tool_errors,
                    ),
                ),
                elapsed_seconds=round(time.monotonic() - dispatch_started, 2),
            )
            return ToolResult(
                content=content,
                structured=success_response.model_dump(),
                metadata={
                    "summary": f"subagent {result.name!r} completed",
                    "subagent_name": result.name,
                    "subagent_type": result.name,
                    "turns_used": result.turns_used,
                    "tool_calls": result.tool_calls,
                    "tool_errors": result.tool_errors,
                    "mode": SpawnJoinMode.DELEGATE.value,
                    "join_mode": SubagentJoinMode.SYNC.value,
                    "background_forced_sync": bool(forced_sync_note),
                },
            )

        outputs = tuple(
            SpawnTaskOutput(
                subagent_type=result.name,
                status=(
                    SpawnDispatchStatus.COMPLETED if result.success else SpawnDispatchStatus.FAILED
                ),
                output=result.output,
                error=result.error,
                turns_used=result.turns_used,
                tool_calls=result.tool_calls,
                tool_errors=result.tool_errors,
            )
            for result in results
        )
        batch_response = SpawnBatchOutput(
            status=(
                SpawnDispatchStatus.COMPLETED
                if all(result.success for result in results)
                else SpawnDispatchStatus.FAILED
            ),
            mode=SubagentJoinMode.SYNC,
            results=outputs,
            elapsed_seconds=round(time.monotonic() - dispatch_started, 2),
        )
        content = batch_response.model_dump_json()
        if forced_sync_note:
            content += f"\n\n[note: background=true forced sync — {forced_sync_note}.]"
        return ToolResult(
            content=content,
            structured=batch_response.model_dump(),
            is_error=not all(result.success for result in results),
            metadata={
                "summary": f"completed {len(results)} subagent(s)",
                "subagent_name": "batch",
                "mode": SubagentJoinMode.SYNC.value,
                "join_mode": SubagentJoinMode.SYNC.value,
                "background_forced_sync": bool(forced_sync_note),
            },
        )
