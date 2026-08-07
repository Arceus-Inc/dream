"""browser_run — CDP-first Chromium via browser-harness."""

from __future__ import annotations

import os
import stat
import textwrap
from pathlib import Path

import pytest

from dream.tools._base import derive_observation
from dream.tools._context import ToolExecutionContext
from dream.tools.browser_run._types import (
    BROWSER_RUN_BIN_KEY,
    BROWSER_RUN_CDP_URL_KEY,
    BrowserRunStatus,
)
from dream.tools.builtin.browser_run import BrowserRunTool


@pytest.fixture
def tool() -> BrowserRunTool:
    return BrowserRunTool()


@pytest.fixture
def fake_harness(tmp_path: Path) -> Path:
    """Minimal browser-harness stand-in: exec stdin, print JSON page_info shape."""
    script = tmp_path / "browser-harness"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json, os, sys
            code = sys.stdin.read()
            # Echo env for assertions when script asks
            if "print_env" in code:
                print(json.dumps({
                    "BU_NAME": os.environ.get("BU_NAME"),
                    "BU_CDP_URL": os.environ.get("BU_CDP_URL"),
                    "BU_CDP_WS": os.environ.get("BU_CDP_WS"),
                    "BROWSER_USE_API_KEY": os.environ.get("BROWSER_USE_API_KEY"),
                }))
                raise SystemExit(0)
            ns = {}
            exec(code, ns, ns)
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.fixture
def ctx(tmp_path: Path, fake_harness: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        working_dir=tmp_path,
        session_id="s_browser",
        metadata={
            BROWSER_RUN_BIN_KEY: str(fake_harness),
            BROWSER_RUN_CDP_URL_KEY: "http://127.0.0.1:9222",
        },
    )


def test_declaration_external_read_only(tool: BrowserRunTool) -> None:
    assert tool.name == "browser_run"
    assert tool.declaration.risk == "external"
    assert tool.declaration.tier_required == 2
    assert tool.is_read_only() is True
    assert tool.is_read_only_for({"code": "print(1)"}) is True


async def test_refused_empty_code(tool: BrowserRunTool, ctx: ToolExecutionContext) -> None:
    result = await tool.execute({"code": "  "}, ctx)
    assert result.is_error is True
    assert result.structured["status"] == BrowserRunStatus.REFUSED.value
    assert "root_cause" in result.metadata


async def test_refused_missing_cdp(tool: BrowserRunTool, tmp_path: Path, fake_harness: Path) -> None:
    bare = ToolExecutionContext(
        working_dir=tmp_path,
        session_id="s",
        metadata={BROWSER_RUN_BIN_KEY: str(fake_harness)},
    )
    # Clear process env so resolve_cdp fails closed.
    old_url = os.environ.pop("DREAM_CHROMIUM_CDP_URL", None)
    old_ws = os.environ.pop("DREAM_CHROMIUM_CDP_WS", None)
    try:
        result = await tool.execute({"code": "print(1)"}, bare)
    finally:
        if old_url is not None:
            os.environ["DREAM_CHROMIUM_CDP_URL"] = old_url
        if old_ws is not None:
            os.environ["DREAM_CHROMIUM_CDP_WS"] = old_ws
    assert result.is_error is True
    assert "CDP" in result.content or "Chromium" in result.content


async def test_refuses_cloud_admin(tool: BrowserRunTool, ctx: ToolExecutionContext) -> None:
    result = await tool.execute(
        {"code": 'start_remote_daemon("r7k2")\nprint(1)'},
        ctx,
    )
    assert result.is_error is True
    assert result.structured["status"] == BrowserRunStatus.REFUSED.value
    assert "Cloud" in result.content


async def test_runs_and_parses_page_json(
    tool: BrowserRunTool, ctx: ToolExecutionContext
) -> None:
    code = (
        'import json\n'
        'print(json.dumps({"page": {"url": "https://example.com", "title": "Example"}}))\n'
    )
    result = await tool.execute({"code": code, "name": "beat1"}, ctx)
    assert result.is_error is False
    assert result.structured["status"] == BrowserRunStatus.SUCCESS.value
    assert result.structured["page"]["url"] == "https://example.com"
    assert result.structured["bu_name"] == "beat1"
    assert result.structured["browser_kind"] == "cdp"
    assert "example.com" in result.metadata["summary"]
    obs = derive_observation(result)
    assert obs.status == "success"


async def test_injects_cdp_env_and_strips_cloud_key(
    tool: BrowserRunTool, ctx: ToolExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BROWSER_USE_API_KEY", "secret-should-not-pass")
    result = await tool.execute(
        {"code": "print_env = True\n", "name": "ns1"},
        ctx,
    )
    assert result.is_error is False
    assert '"BU_NAME": "ns1"' in result.content
    assert '"BU_CDP_URL": "http://127.0.0.1:9222"' in result.content
    assert '"BROWSER_USE_API_KEY": null' in result.content or "BROWSER_USE_API_KEY\": null" in (
        result.content
    )


async def test_nonzero_exit_is_script_error(
    tool: BrowserRunTool, ctx: ToolExecutionContext
) -> None:
    result = await tool.execute({"code": "raise SystemExit(7)"}, ctx)
    assert result.is_error is True
    assert result.structured["status"] == BrowserRunStatus.SCRIPT_ERROR.value
    assert result.metadata["returncode"] == 7


async def test_timeout_kills_process(tool: BrowserRunTool, ctx: ToolExecutionContext) -> None:
    result = await tool.execute(
        {
            "code": "import time\ntime.sleep(30)\nprint('late')\n",
            "timeout_seconds": 0.3,
        },
        ctx,
    )
    assert result.is_error is True
    assert result.structured["status"] == BrowserRunStatus.TIMEOUT.value


def test_browser_pack_registers_browser_run() -> None:
    from dream.tools.builtin import default_registry, register_browser_tools

    reg = default_registry()
    assert reg.get("browser_run") is None
    register_browser_tools(reg)
    assert reg.get("browser_run") is not None
    assert reg.get("browser_run").name == "browser_run"
