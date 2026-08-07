"""Public construct-and-run API — ``dream.build_harness`` (the SDK entrypoint).

Consumers (chorus/lattice/horizon, evals, examples) must be able to build a
*runnable* Harness from the public surface alone: explicit credentials and
model, no ``DREAM_SMOKE_*`` env coupling, no imports from ``dream.repl``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from dream import Harness, SessionOptions, build_harness
from dream.contracts.tool import ToolResult
from dream.engine._engine import QueryEngine
from dream.services.tool_outputs import DEFAULT_TOOL_OUTPUT_INLINE_CHARS
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolSource
from tests.test_skills._helpers import write_skill


class _LargeOutputInput(BaseModel):
    pass


class _LargeOutputTool(BaseTool):
    name = "large_output"
    description = "Return enough text to exercise output offloading."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _LargeOutputInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="x" * (DEFAULT_TOOL_OUTPUT_INLINE_CHARS * 2))


def _system_prompt(harness: Harness) -> str:
    """The system prompt the factory's engine binds for a fresh session."""
    engine = harness.config._engine_factory("s_probe", SessionOptions())  # type: ignore[misc]
    # FailoverStreamer → SubstrateSlot → OpenAIChatStreamer holds the prompt.
    streamer = engine.streamer
    slots = streamer._slots  # type: ignore[attr-defined]
    for slot in slots.values():
        for inner in slot.streamers.values():
            return inner._system_prompt or ""  # type: ignore[attr-defined]
    return ""


def _build(tmp_path: Path, **overrides: object) -> Harness:
    kwargs: dict = {
        "model": "test-model",
        "api_key": "test-key",
        "working_dir": tmp_path / "wt",
        "env": {"DREAM_HOME": str(tmp_path / "home")},
    }
    kwargs.update(overrides)
    (tmp_path / "wt").mkdir(parents=True, exist_ok=True)
    return build_harness(**kwargs)


def test_skills_auto_wired_from_workspace(tmp_path: Path) -> None:
    # "Constitute everything": a skill in the workspace must reach run_task's
    # action surface by default — its catalogue lands in the system prompt
    # with no caller wiring.
    write_skill(tmp_path / "wt" / "docs" / "skills", "weather-lookup")
    harness = _build(tmp_path)
    assert "weather-lookup" in _system_prompt(harness)


def test_skills_can_be_disabled(tmp_path: Path) -> None:
    write_skill(tmp_path / "wt" / "docs" / "skills", "weather-lookup")
    harness = _build(tmp_path, skills=False)
    assert "weather-lookup" not in _system_prompt(harness)


def test_explicit_skill_registry_wins_over_autowire(tmp_path: Path) -> None:
    # A caller-supplied registry is authoritative — auto-wire must not
    # override it (the REPL builds its own with shadow reporting).
    from dream.skills import SkillRegistry

    write_skill(tmp_path / "wt" / "docs" / "skills", "weather-lookup")
    harness = _build(tmp_path, skill_registry=SkillRegistry())
    assert "weather-lookup" not in _system_prompt(harness)


def _write_memory_record(tmp_path: Path, record_id: str, *, description: str, body: str) -> None:
    """Write a markdown memory record into the workspace's project memory dir.

    Mirrors ``_build``'s home/working_dir so the factory's
    ``project_memory_dir(paths.home, working_dir)`` resolves to the same place.
    """
    from dream.memory import project_memory_dir

    memory_dir = project_memory_dir(tmp_path / "home", tmp_path / "wt")
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / f"{record_id}.md").write_text(
        f"---\nname: {record_id}\ndescription: {description}\n"
        f"metadata:\n  type: project\n  scope: project\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_memory_catalogue_auto_wired_from_workspace(tmp_path: Path) -> None:
    # A memory record in the workspace's project memory dir must reach
    # run_task's system prompt by default — its id + description land in the
    # catalogue with no caller wiring.
    _write_memory_record(
        tmp_path,
        "naming-convention",
        description="services use a service- prefix",
        body="Name services service-<domain>.",
    )
    prompt = _system_prompt(_build(tmp_path))
    assert "naming-convention" in prompt
    assert "services use a service- prefix" in prompt


def test_memory_can_be_disabled(tmp_path: Path) -> None:
    _write_memory_record(
        tmp_path,
        "naming-convention",
        description="services use a service- prefix",
        body="Name services service-<domain>.",
    )
    prompt = _system_prompt(_build(tmp_path, memory=False))
    assert "naming-convention" not in prompt


def test_memory_tools_registered_when_memory_enabled(tmp_path: Path) -> None:
    from dream.tools.builtin import default_registry

    registry = default_registry()
    assert "memory_search" not in {t.name for t in registry.list_tools()}
    _build(tmp_path, registry=registry, memory=True)
    names = {t.name for t in registry.list_tools()}
    assert "memory_search" in names
    assert "memory_get" in names


def test_memory_tools_omitted_when_memory_disabled(tmp_path: Path) -> None:
    from dream.tools.builtin import default_registry

    registry = default_registry()
    _build(tmp_path, registry=registry, memory=False)
    names = {t.name for t in registry.list_tools()}
    assert "memory_search" not in names
    assert "memory_get" not in names


def test_build_harness_omits_pack_tools_by_default(tmp_path: Path) -> None:
    from dream.tools.builtin import default_registry

    registry = default_registry()
    _build(tmp_path, registry=registry, memory=False)
    names = {t.name for t in registry.list_tools()}
    for pack_tool in ("task_create", "web_search", "browser_run", "execute_code", "plan_show"):
        assert pack_tool not in names


def test_build_harness_legacy_surface_registers_packs(tmp_path: Path) -> None:
    from dream.tools.builtin import default_registry

    registry = default_registry()
    _build(tmp_path, registry=registry, legacy_surface=True)
    names = {t.name for t in registry.list_tools()}
    assert {"memory_search", "task_create", "web_search", "execute_code"} <= names


def test_build_harness_individual_pack_flags(tmp_path: Path) -> None:
    from dream.tools.builtin import default_registry

    registry = default_registry()
    _build(tmp_path, registry=registry, memory=False, web=True, plan=True)
    names = {t.name for t in registry.list_tools()}
    assert {"web_search", "web_fetch", "plan_show"} <= names
    assert "task_create" not in names
    assert "browser_run" not in names


_TASK_MEMORY_TOOLS = {
    "working_memory_read",
    "working_memory_write",
    "working_memory_append",
    "memory_propose",
}


def test_build_harness_default_omits_task_memory_tools(tmp_path: Path) -> None:
    # Task memory is opt-in: the default surface must stay unchanged.
    from dream.tools.builtin import default_registry

    registry = default_registry()
    _build(tmp_path, registry=registry)
    names = {t.name for t in registry.list_tools()}
    assert names.isdisjoint(_TASK_MEMORY_TOOLS)


def test_build_harness_working_memory_flag_registers_tools(tmp_path: Path) -> None:
    from dream.tools.builtin import default_registry

    registry = default_registry()
    _build(tmp_path, registry=registry, working_memory=True)
    names = {t.name for t in registry.list_tools()}
    assert _TASK_MEMORY_TOOLS <= names


def test_build_harness_working_memory_wires_context(tmp_path: Path) -> None:
    from dream.memory import TASK_MEMORY_CONTEXT_KEY, TaskMemoryContext

    harness = _build(tmp_path, working_memory=True)
    engine = harness.config._engine_factory("s_probe", SessionOptions())  # type: ignore[misc]
    context = engine.dispatcher.context_metadata[TASK_MEMORY_CONTEXT_KEY]  # type: ignore[attr-defined]
    assert isinstance(context, TaskMemoryContext)
    assert context.working_memory.path.name == "working-memory.md"
    assert context.proposals_dir.name == "_proposals"


def test_build_harness_default_omits_task_memory_context(tmp_path: Path) -> None:
    from dream.memory import TASK_MEMORY_CONTEXT_KEY

    harness = _build(tmp_path)
    engine = harness.config._engine_factory("s_probe", SessionOptions())  # type: ignore[misc]
    assert TASK_MEMORY_CONTEXT_KEY not in engine.dispatcher.context_metadata  # type: ignore[attr-defined]


def test_build_harness_wires_session_scratch_for_offload_retrieval(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    engine = harness.config._engine_factory("s_probe", SessionOptions())  # type: ignore[misc]

    assert engine.dispatcher.scratch_dir == (  # type: ignore[attr-defined]
        tmp_path / "wt" / ".dream" / "sidecars" / "s_probe" / "scratch"
    )


async def test_build_harness_retrieves_output_spilled_without_explicit_scratch(
    tmp_path: Path,
) -> None:
    from dream.tools.builtin import default_registry

    registry = default_registry()
    registry.register(_LargeOutputTool(), source=ToolSource.DEFAULT)
    harness = _build(tmp_path, registry=registry)
    engine = harness.config._engine_factory("s_probe", SessionOptions())  # type: ignore[misc]

    pointer, spill_error = await engine.dispatcher.dispatch("large_output", {})  # type: ignore[attr-defined]
    scratch = engine.dispatcher.scratch_dir  # type: ignore[attr-defined]
    assert spill_error is False
    assert scratch is not None
    [artifact] = scratch.iterdir()
    assert artifact.name in pointer

    content, read_error = await engine.dispatcher.dispatch(  # type: ignore[attr-defined]
        "read_offloaded", {"path": artifact.name, "start": 100, "end": 1100}
    )
    assert read_error is False
    assert content == "x" * 1000


def test_register_task_memory_tools_is_idempotent() -> None:
    from dream.tools.builtin import default_registry, register_task_memory_tools

    registry = default_registry()
    register_task_memory_tools(registry)
    register_task_memory_tools(registry)  # second call must not raise a collision
    names = {t.name for t in registry.list_tools()}
    assert _TASK_MEMORY_TOOLS <= names


def test_per_repo_shadow_keeps_task_memory_tools(tmp_path: Path) -> None:
    from dream import build_harness
    from dream.tools._registry import ToolSource
    from dream.tools.builtin import default_registry

    wt = tmp_path / "wt"
    tools_dir = wt / ".harness" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "working_memory_read.toml").write_text(
        """
name = "working_memory_read"
description = "Repo-local memory reader"
command = "echo repo"
risk = "safe"
tier_required = 0
timeout_seconds = 5.0
parameters = { type = "object", properties = {} }
""",
        encoding="utf-8",
    )
    registry = default_registry()
    build_harness(
        model="m",
        api_key="k",
        working_dir=wt,
        registry=registry,
        memory=False,
        working_memory=True,
        env={"DREAM_HOME": str(tmp_path / "home")},
    )
    assert {t.name for t in registry.list_tools()} >= _TASK_MEMORY_TOOLS
    sources = {tool.name: source for tool, source in registry.iter_with_source()}
    assert sources["working_memory_read"] is ToolSource.PER_REPO


def test_build_harness_is_public() -> None:
    import dream

    assert "build_harness" in dream.__all__


def test_returns_runnable_harness(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    assert isinstance(harness, Harness)
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("s_test", SessionOptions())
    assert isinstance(engine, QueryEngine)


def test_no_smoke_env_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The public builder must not read DREAM_SMOKE_* at all.
    for var in ("DREAM_SMOKE_API_KEY", "DREAM_SMOKE_MODEL", "DREAM_SMOKE_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    harness = _build(tmp_path)
    assert harness.config._engine_factory is not None


def test_task_manager_and_cron_registry_wired(tmp_path: Path) -> None:
    from dream.tasks import BackgroundTaskManager

    harness = _build(tmp_path)
    assert isinstance(harness.config.task_manager, BackgroundTaskManager)
    assert isinstance(harness.config.cron_registry_path, Path)


def test_sandbox_adapter_wired_into_session_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Spec 13B: the selected SandboxAdapter must ride the session's
    # context_metadata so the ``bash`` tool executes through the one backend.
    # Default is docker; subprocess is opt-in via sandbox.toml ``backend``.
    from dream.sandbox import SANDBOX_CONTEXT_KEY, DockerSandbox, SandboxAdapter
    from dream.sandbox.docker_backend import DockerAvailability

    monkeypatch.setattr(
        "dream.sandbox.get_docker_availability",
        lambda: DockerAvailability(available=True, command="/usr/bin/docker"),
    )
    harness = _build(tmp_path)
    engine = harness.config._engine_factory("s_sbx", SessionOptions())  # type: ignore[misc]
    adapter = engine.dispatcher.context_metadata[SANDBOX_CONTEXT_KEY]  # type: ignore[attr-defined]
    assert isinstance(adapter, SandboxAdapter)
    assert isinstance(adapter, DockerSandbox)


def test_sandbox_adapter_subprocess_when_configured(tmp_path: Path) -> None:
    from dream.sandbox import SANDBOX_CONTEXT_KEY, SubprocessSandbox

    harness_dir = tmp_path / "wt" / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "sandbox.toml").write_text(
        'backend = "subprocess"\n',
        encoding="utf-8",
    )
    harness = _build(tmp_path)
    engine = harness.config._engine_factory("s_sbx_sp", SessionOptions())  # type: ignore[misc]
    adapter = engine.dispatcher.context_metadata[SANDBOX_CONTEXT_KEY]  # type: ignore[attr-defined]
    assert isinstance(adapter, SubprocessSandbox)


def test_sandbox_adapter_docker_image_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dream.sandbox import SANDBOX_CONTEXT_KEY, DockerSandbox
    from dream.sandbox.docker_backend import DockerAvailability

    monkeypatch.setattr(
        "dream.sandbox.get_docker_availability",
        lambda: DockerAvailability(available=True, command="/usr/bin/docker"),
    )
    harness_dir = tmp_path / "wt" / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "sandbox.toml").write_text(
        '[docker]\nimage = "dream-sandbox:test"\n',
        encoding="utf-8",
    )
    harness = _build(tmp_path)
    engine = harness.config._engine_factory("s_sbx_d", SessionOptions())  # type: ignore[misc]
    adapter = engine.dispatcher.context_metadata[SANDBOX_CONTEXT_KEY]  # type: ignore[attr-defined]
    assert isinstance(adapter, DockerSandbox)
    assert adapter.config.image == "dream-sandbox:test"


def test_sandbox_soft_degrades_when_docker_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dream.sandbox import SANDBOX_CONTEXT_KEY, SubprocessSandbox
    from dream.sandbox.docker_backend import DockerAvailability

    monkeypatch.setattr(
        "dream.sandbox.get_docker_availability",
        lambda: DockerAvailability(available=False, reason="Docker daemon is not running"),
    )
    harness = _build(tmp_path)
    engine = harness.config._engine_factory("s_sbx_soft", SessionOptions())  # type: ignore[misc]
    adapter = engine.dispatcher.context_metadata[SANDBOX_CONTEXT_KEY]  # type: ignore[attr-defined]
    assert isinstance(adapter, SubprocessSandbox)


def test_sandbox_raises_when_fail_if_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dream.errors import SandboxError
    from dream.sandbox.docker_backend import DockerAvailability

    monkeypatch.setattr(
        "dream.sandbox.get_docker_availability",
        lambda: DockerAvailability(available=False, reason="Docker daemon is not running"),
    )
    harness_dir = tmp_path / "wt" / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "sandbox.toml").write_text(
        "[docker]\nfail_if_unavailable = true\n",
        encoding="utf-8",
    )
    with pytest.raises(SandboxError, match="not running"):
        _build(tmp_path)


def test_extra_no_longer_smuggles_runtime_fields(tmp_path: Path) -> None:
    # The typed fields replace the ``config.extra`` escape hatch.
    harness = _build(tmp_path)
    assert "task_manager" not in harness.config.extra
    assert "cron_registry_path" not in harness.config.extra


def test_dream_home_override_honoured(tmp_path: Path) -> None:
    # Paths resolved from the provided env mapping, not os.environ.
    _build(tmp_path)
    assert (tmp_path / "home").exists()


def test_empty_model_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model"):
        _build(tmp_path, model="")


def test_empty_api_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="api_key"):
        _build(tmp_path, api_key="")


def test_working_dir_recorded_on_config(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    assert harness.config.working_dir == tmp_path / "wt"
