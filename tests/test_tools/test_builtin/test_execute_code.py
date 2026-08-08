"""Hermes-style execute_code — mechanical multi-step collapse (SOTA #10).

Parent LLM sees one tool result (stdout + call count). Nested tool I/O stays
on the RPC path and never becomes parent conversation messages.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin import default_registry
from dream.tools.builtin.execute_code import ExecuteCodeTool
from dream.tools.execute_code import (
    EXECUTE_CODE_REGISTRY_KEY,
    NestedToolName,
    RegistryToolInvoker,
    sandbox_tools_for,
)


@pytest.fixture
def registry():
    from dream.tools.builtin import register_code_intel_tools, register_web_tools

    reg = default_registry()
    register_code_intel_tools(reg)
    # Nested allowlist may include web_* when present in the session registry.
    register_web_tools(reg)
    return reg


@pytest.fixture
def ctx(tmp_path: Path, registry) -> ToolExecutionContext:
    return ToolExecutionContext(
        working_dir=tmp_path,
        session_id="s_execute_code",
        metadata={EXECUTE_CODE_REGISTRY_KEY: registry},
    )


@pytest.fixture
def tool() -> ExecuteCodeTool:
    return ExecuteCodeTool()


def test_declaration_is_mutating(tool: ExecuteCodeTool) -> None:
    assert tool.name == "execute_code"
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required >= 1


def test_sandbox_allowlist_is_typed_dream_names() -> None:
    from dream.tools.builtin import register_code_intel_tools

    reg = default_registry()
    register_code_intel_tools(reg)
    session = frozenset(t.name for t in reg.list_tools())
    names = sandbox_tools_for(session)
    assert NestedToolName.READ_FILE in names
    assert NestedToolName.BASH in names
    # execute_code is never a nested surface (no recursion via RPC).
    assert "execute_code" not in {n.value for n in names}


def test_empty_session_intersection_fails_closed() -> None:
    assert sandbox_tools_for(frozenset()) == frozenset()


def test_code_intel_pack_includes_execute_code() -> None:
    from dream.tools.builtin import register_code_intel_tools

    reg = default_registry()
    register_code_intel_tools(reg)
    names = {t.name for t in reg.list_tools()}
    assert "execute_code" in names
    assert "execute_code" not in {t.name for t in default_registry().list_tools()}

async def test_script_stdout_is_the_only_parent_payload(
    tool: ExecuteCodeTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    (tmp_path / "note.txt").write_text("hello-collapse", encoding="utf-8")
    code = (
        "from dream_tools import read_file\n"
        "text = read_file(path='note.txt')\n"
        "print(text.strip())\n"
    )
    result = await tool.execute({"code": code}, ctx)

    assert result.is_error is False
    assert "hello-collapse" in result.content
    assert "tool_calls_made" in result.metadata
    assert result.metadata["tool_calls_made"] == 1
    # Nested tool chatter must not leak as parent content beyond printed stdout.
    assert "root_cause" not in result.content


async def test_nested_write_then_read_collapses_to_one_result(
    tool: ExecuteCodeTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    code = (
        "from dream_tools import write_file, read_file\n"
        "write_file(path='out.txt', content='alpha\\n')\n"
        "print(read_file(path='out.txt').strip())\n"
    )
    result = await tool.execute({"code": code}, ctx)

    assert result.is_error is False
    assert result.metadata["tool_calls_made"] == 2
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "alpha\n"
    assert result.content.strip().endswith("alpha")


async def test_disallowed_tool_is_refused_before_dispatch(
    tool: ExecuteCodeTool, ctx: ToolExecutionContext
) -> None:
    code = (
        "from dream_tools import skill\n"  # not in sandbox allowlist
        "print(skill(name='x'))\n"
    )
    result = await tool.execute({"code": code}, ctx)
    assert result.is_error is True
    assert result.metadata["tool_calls_made"] == 0


async def test_missing_registry_fails_closed(tool: ExecuteCodeTool, tmp_path: Path) -> None:
    bare = ToolExecutionContext(working_dir=tmp_path, session_id="s_bare")
    result = await tool.execute({"code": "print('x')"}, bare)
    assert result.is_error is True
    assert "invoker" in result.content.lower() or "registry" in result.content.lower()


async def test_tool_call_cap_stops_runaway(
    tool: ExecuteCodeTool, ctx: ToolExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dream.tools.execute_code import _session as session_mod

    monkeypatch.setattr(session_mod, "DEFAULT_MAX_TOOL_CALLS", 2)
    code = (
        "from dream_tools import read_file\n"
        "for i in range(5):\n"
        "    read_file(path='missing.txt')\n"
        "print('done')\n"
    )
    result = await tool.execute({"code": code}, ctx)
    assert result.is_error is True
    assert result.metadata["tool_calls_made"] == 2
    assert result.metadata["status"] == "cap_exceeded"


async def test_outcome_structured_is_typed_shape(
    tool: ExecuteCodeTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_text("z", encoding="utf-8")
    result = await tool.execute(
        {
            "code": "from dream_tools import read_file\nprint(read_file(path='a.txt'))\n",
        },
        ctx,
    )
    assert result.structured is not None
    assert result.structured["status"] == "success"
    assert isinstance(result.structured["tool_calls_made"], int)
    assert isinstance(result.structured["exit_code"], int)


async def test_invoker_rejects_unknown_tool_name(registry, ctx: ToolExecutionContext) -> None:
    invoker = RegistryToolInvoker(
        registry=registry,
        context=ctx,
        allowed=frozenset({NestedToolName.READ_FILE}),
        max_calls=5,
    )
    with pytest.raises(PermissionError):
        await invoker.invoke(NestedToolName.BASH, {"command": "echo hi"})


def test_guard_blocks_subprocess_import() -> None:
    from dream.tools.execute_code import check_execute_code_guard

    msg = check_execute_code_guard("import subprocess\nsubprocess.run(['echo', 'x'])\n")
    assert msg is not None
    assert "subprocess" in msg


def test_guard_allows_dream_tools_imports() -> None:
    from dream.tools.execute_code import check_execute_code_guard

    code = (
        "from dream_tools import read_file, bash\n"
        "print(read_file(path='a.txt'))\n"
        "print(bash(command='echo hi'))\n"
    )
    assert check_execute_code_guard(code) is None


def test_hygiene_strips_ansi_and_redacts_secrets() -> None:
    from dream.tools.execute_code._hygiene import sanitize_output

    raw = "\x1b[31msecret\x1b[0m API_TOKEN=supersecretvalue1234567890"
    cleaned = sanitize_output(raw)
    assert "\x1b" not in cleaned
    assert "supersecretvalue1234567890" not in cleaned
    assert "***REDACTED***" in cleaned


def test_observation_helpers_cover_statuses() -> None:
    from dream.tools.execute_code._observation import next_actions_for, summary_for
    from dream.tools.execute_code._types import ExecuteCodeStatus

    assert summary_for(
        ExecuteCodeStatus.SUCCESS, exit_code=0, tool_calls_made=1
    ).startswith("execute_code success")
    assert next_actions_for(ExecuteCodeStatus.SUCCESS) == []
    assert next_actions_for(ExecuteCodeStatus.TIMEOUT)
    assert next_actions_for(ExecuteCodeStatus.REFUSED)
    assert next_actions_for(ExecuteCodeStatus.CANCELLED)


async def test_guard_refusal_surfaces_on_tool(
    tool: ExecuteCodeTool, ctx: ToolExecutionContext
) -> None:
    result = await tool.execute(
        {"code": "import subprocess\nprint(subprocess.getoutput('echo hi'))\n"},
        ctx,
    )
    assert result.is_error is True
    assert result.metadata["status"] == "refused"
    assert "subprocess" in result.content
    assert result.structured is not None
    assert result.structured["summary"]
    assert result.structured["next_actions"]


async def test_outcome_includes_summary_and_log_fields(
    tool: ExecuteCodeTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_text("z", encoding="utf-8")
    result = await tool.execute(
        {"code": "from dream_tools import read_file\nprint(read_file(path='a.txt'))\n"},
        ctx,
    )
    assert result.structured is not None
    assert "summary" in result.structured
    assert "next_actions" in result.structured
    assert "tool_call_log" in result.structured
    assert isinstance(result.structured["tool_call_log"], list)
