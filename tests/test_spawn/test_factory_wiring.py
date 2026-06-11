"""Factory wiring tests for spawn_subagent feature.

Tests written FIRST (RED), before implementation exists.
Covers:
- spawn context present in default session context_metadata
- absent with spawn=False
- absent when session has a subagent-named manifest
- spawn_subagent in registry order pins (18 -> 19)
- spawn_subagent absent from child wire schema
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dream import build_harness
from dream.roles._manifest import RoleManifest
from dream.runner._role_session import ROLE_MANIFEST_METADATA_KEY
from dream.session import SessionOptions
from dream.spawn._context import SPAWN_CONTEXT_KEY, SpawnContext


def _build(tmp_path: Path, **overrides: object) -> Any:
    kwargs: dict[str, Any] = {
        "model": "test-model",
        "api_key": "test-key",
        "working_dir": tmp_path / "wt",
        "env": {"DREAM_HOME": str(tmp_path / "home")},
    }
    kwargs.update(overrides)
    (tmp_path / "wt").mkdir(parents=True, exist_ok=True)
    return build_harness(**kwargs)


def _get_engine(harness: Any, session_id: str = "s_probe", options: SessionOptions | None = None) -> Any:
    opts = options or SessionOptions()
    return harness.config._engine_factory(session_id, opts)


# ---------------------------------------------------------------------------
# spawn context present in default session
# ---------------------------------------------------------------------------


def test_spawn_context_present_in_default_session(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    engine = _get_engine(harness)
    metadata = engine.dispatcher.context_metadata  # type: ignore[attr-defined]
    ctx = metadata.get(SPAWN_CONTEXT_KEY)
    assert isinstance(ctx, SpawnContext)


def test_spawn_context_has_budget(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    engine = _get_engine(harness)
    metadata = engine.dispatcher.context_metadata  # type: ignore[attr-defined]
    ctx = metadata[SPAWN_CONTEXT_KEY]
    assert ctx.budget is not None


def test_spawn_context_has_callable_spawn(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    engine = _get_engine(harness)
    metadata = engine.dispatcher.context_metadata  # type: ignore[attr-defined]
    ctx = metadata[SPAWN_CONTEXT_KEY]
    assert callable(ctx.spawn)


# ---------------------------------------------------------------------------
# spawn=False omits context
# ---------------------------------------------------------------------------


def test_spawn_false_omits_spawn_context(tmp_path: Path) -> None:
    harness = _build(tmp_path, spawn=False)
    engine = _get_engine(harness)
    metadata = engine.dispatcher.context_metadata  # type: ignore[attr-defined]
    assert SPAWN_CONTEXT_KEY not in metadata


# ---------------------------------------------------------------------------
# subagent manifest omits context
# ---------------------------------------------------------------------------


def test_subagent_session_omits_spawn_context(tmp_path: Path) -> None:
    """A session whose metadata carries a subagent RoleManifest must NOT get SpawnContext."""
    harness = _build(tmp_path)

    subagent_manifest = RoleManifest(
        name="subagent",  # type: ignore[arg-type]
        description="child",
        system_prompt="you are a child",
        tools=("read_file",),
        disallowed_tools=("spawn_subagent",),
    )
    opts = SessionOptions(metadata={ROLE_MANIFEST_METADATA_KEY: subagent_manifest})
    engine = _get_engine(harness, options=opts)
    metadata = engine.dispatcher.context_metadata  # type: ignore[attr-defined]
    assert SPAWN_CONTEXT_KEY not in metadata


# ---------------------------------------------------------------------------
# default registry now has 19 tools (18 -> 19)
# ---------------------------------------------------------------------------


def test_default_registry_has_19_tools() -> None:
    from dream.tools.builtin import default_registry

    tools = default_registry().list_tools()
    assert len(tools) == 19


def test_spawn_subagent_in_default_registry() -> None:
    from dream.tools.builtin import default_registry

    names = {t.name for t in default_registry().list_tools()}
    assert "spawn_subagent" in names


# ---------------------------------------------------------------------------
# spawn_subagent absent from child wire schema (subagent manifest disallows it)
# ---------------------------------------------------------------------------


def test_subagent_manifest_disallows_spawn_subagent(tmp_path: Path) -> None:
    """The synthesized subagent manifest must exclude spawn_subagent from allowed tools."""
    from dream.config.paths import DreamPaths
    from dream.engine._permission_gate import compute_session_role_allowlist
    from dream.tools.builtin import default_registry

    subagent_manifest = RoleManifest(
        name="subagent",  # type: ignore[arg-type]
        description="child",
        system_prompt="you are a child",
        tools=("read_file", "bash"),
        disallowed_tools=("spawn_subagent",),
    )

    working_dir = tmp_path / "wt"
    working_dir.mkdir(parents=True, exist_ok=True)
    paths = DreamPaths.resolve(working_dir, env={"DREAM_HOME": str(tmp_path / "home")})

    registry = default_registry()
    allowed = compute_session_role_allowlist(
        registry,
        paths=paths,
        cwd=working_dir,
        manifest=subagent_manifest,
    )
    assert "spawn_subagent" not in allowed


# ---------------------------------------------------------------------------
# wire-schema visibility: a session that cannot spawn must not SEE the tool
# (otherwise the model dispatches it and burns a turn on the unavailable error)
# ---------------------------------------------------------------------------


def _skill_available_tools(engine: Any) -> frozenset[str]:
    """The per-session available-tool set — built from the same filtered tool
    list as the wire schema, so it is the observable proxy for what the model
    can see."""
    from dream.skills import SKILL_CONTEXT_KEY

    skill_ctx = engine.dispatcher.context_metadata.get(SKILL_CONTEXT_KEY)
    assert skill_ctx is not None, "skills must be wired for this probe"
    return skill_ctx.available_tools


def test_spawn_tool_visible_in_default_session(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    engine = _get_engine(harness)
    assert "spawn_subagent" in _skill_available_tools(engine)


def test_spawn_tool_hidden_when_spawn_false(tmp_path: Path) -> None:
    harness = _build(tmp_path, spawn=False)
    engine = _get_engine(harness)
    assert "spawn_subagent" not in _skill_available_tools(engine)


def test_spawn_tool_hidden_in_subagent_session(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    manifest = RoleManifest(
        name="subagent",
        description="child",
        system_prompt="x",
        tools=None,
        disallowed_tools=("spawn_subagent",),
    )
    options = SessionOptions(metadata={ROLE_MANIFEST_METADATA_KEY: manifest})
    engine = _get_engine(harness, "s_child", options)
    assert "spawn_subagent" not in _skill_available_tools(engine)
