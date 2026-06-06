"""Spec 05 slice A — ``BaseTool`` ABC + ``Observation`` derivation.

Spec acceptance #1-3 (uniform contract), #7-8 (normalized result, derived
observation built from metadata, **never** from parsing ``content`` prose).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, Observation, ToolDeclaration, derive_observation
from dream.tools._context import ToolExecutionContext


class _EchoInput(BaseModel):
    text: str = Field(..., description="Text to echo back")


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echo a string back unchanged."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _EchoInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=input["text"], metadata={"echoed": True})


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=Path.cwd(), session_id="s_test")


def test_tool_exposes_name_description_declaration() -> None:
    t = _EchoTool()
    assert t.name == "echo"
    assert t.description.startswith("Echo")
    assert t.declaration.risk == "safe"


def test_input_schema_is_derived_from_pydantic_model() -> None:
    t = _EchoTool()
    schema = t.input_schema()
    assert schema["type"] == "object"
    assert "text" in schema["properties"]
    assert schema["properties"]["text"]["type"] == "string"


def test_to_api_schema_shape() -> None:
    t = _EchoTool()
    api = t.to_api_schema()
    assert set(api.keys()) == {"name", "description", "input_schema"}
    assert api["name"] == "echo"
    assert api["input_schema"]["properties"]["text"]["type"] == "string"


def test_is_read_only_defaults_from_declaration_risk() -> None:
    t = _EchoTool()
    assert t.is_read_only() is True


def test_is_read_only_mutating_default_false() -> None:
    class _Mut(BaseTool):
        name = "mut"
        description = "Mutating tool."
        declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=30.0)
        input_model = _EchoInput

        async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
            return ToolResult(content="ok")

    assert _Mut().is_read_only() is False


def test_is_read_only_for_defaults_to_static_value() -> None:
    t = _EchoTool()
    assert t.is_read_only_for({"text": "hi"}) is True


def test_is_read_only_for_can_downclassify() -> None:
    """A mutating tool may declare a specific *invocation* read-only."""

    class _CondBash(BaseTool):
        name = "cond_bash"
        description = "Bash with per-call downclassification."
        declaration = ToolDeclaration(risk="mutating", tier_required=2, timeout_seconds=60.0)
        input_model = _EchoInput

        def is_read_only_for(self, input: dict[str, Any]) -> bool:
            return input.get("text", "").startswith("cat ")

        async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
            return ToolResult(content="")

    t = _CondBash()
    assert t.is_read_only() is False  # declared worst-case
    assert t.is_read_only_for({"text": "cat foo"}) is True  # per-call


def test_subclass_without_declaration_raises_at_class_creation() -> None:
    from dream.tools._base import ToolDeclarationError

    with pytest.raises(ToolDeclarationError):

        class _Bad(BaseTool):
            name = "bad"
            description = "Missing declaration."
            input_model = _EchoInput

            async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
                return ToolResult(content="")

        _ = _Bad  # silence "unused class" lint


def test_subclass_without_name_raises_at_class_creation() -> None:
    from dream.tools._base import ToolDeclarationError

    with pytest.raises(ToolDeclarationError):

        class _Bad(BaseTool):
            description = "Missing name."
            declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
            input_model = _EchoInput

            async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
                return ToolResult(content="")

        _ = _Bad


# ----------------------------------------------------------------------------
# Observation derivation -- spec acceptance #7-8.
# The observation MUST be built from is_error + metadata, NEVER by parsing
# the human-facing ``content`` prose.
# ----------------------------------------------------------------------------


def test_observation_is_success_when_no_error() -> None:
    obs = derive_observation(ToolResult(content="ok", metadata={"returncode": 0}))
    assert obs.status == "success"
    assert obs.summary  # non-empty
    assert obs.next_actions == ()


def test_observation_is_error_when_is_error_true() -> None:
    res = ToolResult(
        content="something went wrong",
        is_error=True,
        metadata={
            "root_cause": "permission denied",
            "safe_retry": "request elevated tier",
            "stop_condition": "after 2 retries",
        },
    )
    obs = derive_observation(res)
    assert obs.status == "error"
    assert "permission denied" in obs.summary or "permission denied" in " ".join(obs.next_actions)
    # The error-recovery contract (acceptance #7) flows through next_actions.
    joined = " | ".join(obs.next_actions)
    assert "request elevated tier" in joined
    assert "after 2 retries" in joined


def test_observation_status_warning_when_metadata_flags_warning() -> None:
    obs = derive_observation(ToolResult(content="done with skips", metadata={"warning": True}))
    assert obs.status == "warning"


def test_observation_summary_uses_metadata_facts_not_content() -> None:
    """The summary MUST NOT depend on parsing the prose content."""
    long_prose = "a" * 5000
    obs = derive_observation(
        ToolResult(
            content=long_prose,
            metadata={"returncode": 0, "lines_changed": 3, "bytes_written": 120},
        )
    )
    # Summary should reference structured facts, not the prose.
    assert long_prose not in obs.summary
    assert "0" in obs.summary or "lines_changed=3" in obs.summary or "3" in obs.summary


def test_observation_artifacts_from_metadata() -> None:
    obs = derive_observation(
        ToolResult(
            content="written",
            metadata={
                "artifacts": ["src/foo.py", "src/bar.py"],
                "offload_ref": "sidecar/abc.txt",
            },
        )
    )
    assert "src/foo.py" in obs.artifacts
    assert "src/bar.py" in obs.artifacts
    assert "sidecar/abc.txt" in obs.artifacts


def test_observation_is_frozen() -> None:
    obs = Observation(status="success", summary="ok", next_actions=(), artifacts=())
    with pytest.raises((AttributeError, TypeError)):
        obs.status = "error"  # type: ignore[misc]
