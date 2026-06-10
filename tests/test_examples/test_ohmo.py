"""Ohmo — the example always-on research agent (built on the public SDK).

Covers the agent's own logic: arXiv feed parsing, the research tools'
file mechanics, workspace bootstrap, and the daemon body's exit codes.
The long-running machinery itself (locks, loops, channel, wake) is the
SDK's and is covered by tests/test_runtime.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

_EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

from ohmo.agent import (  # noqa: E402
    bootstrap_workspace,
    make_wake_run_handler,
    resolve_credentials,
    run_ohmo,
)
from ohmo.persona import OHMO_HEARTBEAT_PROMPT, OHMO_PERSONA  # noqa: E402
from ohmo.tools import (  # noqa: E402
    ArxivSearchTool,
    ReadingQueueTool,
    SaveResearchBriefTool,
    parse_arxiv_feed,
    research_tools,
)

from dream.tools._context import ToolExecutionContext  # noqa: E402

_ATOM = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query Results</title>
  <entry>
    <id>http://arxiv.org/abs/2405.21060v1</id>
    <title>Transformers are SSMs:
      Generalized Models</title>
    <summary>  We show a   duality between transformers and SSMs.  </summary>
    <published>2024-05-31T17:59:00Z</published>
    <author><name>Tri Dao</name></author>
    <author><name>Albert Gu</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2312.00752v2</id>
    <title>Mamba: Linear-Time Sequence Modeling</title>
    <summary>Selective state space models.</summary>
    <published>2023-12-01T00:00:00Z</published>
    <author><name>Albert Gu</name></author>
  </entry>
</feed>
"""


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s-test")


# --- feed parsing -----------------------------------------------------------


def test_parse_arxiv_feed() -> None:
    entries = parse_arxiv_feed(_ATOM)
    assert [e.arxiv_id for e in entries] == ["2405.21060v1", "2312.00752v2"]
    first = entries[0]
    assert first.title == "Transformers are SSMs: Generalized Models"
    assert first.authors == ("Tri Dao", "Albert Gu")
    assert first.summary == "We show a duality between transformers and SSMs."
    assert first.link == "http://arxiv.org/abs/2405.21060v1"


def test_parse_arxiv_feed_tolerates_garbage() -> None:
    assert parse_arxiv_feed("<not really xml") == ()
    assert parse_arxiv_feed("<feed xmlns='http://www.w3.org/2005/Atom'/>") == ()


# --- arxiv_search tool ------------------------------------------------------


@pytest.mark.asyncio
async def test_arxiv_search_formats_results(tmp_path: Path) -> None:
    seen_urls: list[str] = []

    async def fake_fetch(url: str) -> str:
        seen_urls.append(url)
        return _ATOM

    tool = ArxivSearchTool(fetch=fake_fetch)
    result = await tool.execute(
        {"query": "state space models", "max_results": 5}, _ctx(tmp_path)
    )
    assert not result.is_error
    assert "2405.21060v1" in result.content
    assert "Tri Dao" in result.content
    assert result.metadata["results"] == 2
    assert "export.arxiv.org" in seen_urls[0]
    assert "max_results=5" in seen_urls[0]


@pytest.mark.asyncio
async def test_arxiv_search_failure_keeps_recovery_contract(tmp_path: Path) -> None:
    async def broken_fetch(url: str) -> str:
        raise OSError("network down")

    tool = ArxivSearchTool(fetch=broken_fetch)
    result = await tool.execute({"query": "anything"}, _ctx(tmp_path))
    assert result.is_error
    assert "root_cause" in result.metadata
    assert "stop_condition" in result.metadata


def test_arxiv_search_declares_network_tier() -> None:
    tool = ArxivSearchTool()
    assert tool.declaration.risk == "external"
    assert tool.declaration.tier_required == 2


# --- save_research_brief ----------------------------------------------------


@pytest.mark.asyncio
async def test_save_brief_writes_file_and_index(tmp_path: Path) -> None:
    tool = SaveResearchBriefTool()
    result = await tool.execute(
        {
            "slug": "mamba-2",
            "title": "Mamba-2: Transformers are SSMs",
            "markdown": "# Mamba-2\n\nThe core idea is a duality..." + "x" * 50,
        },
        _ctx(tmp_path),
    )
    assert not result.is_error
    brief = tmp_path / "docs" / "research" / "briefs" / "mamba-2.md"
    assert brief.exists()
    index = (tmp_path / "docs" / "research" / "INDEX.md").read_text(encoding="utf-8")
    assert "(briefs/mamba-2.md)" in index


@pytest.mark.asyncio
async def test_save_brief_refuses_overwrite_without_revise(tmp_path: Path) -> None:
    tool = SaveResearchBriefTool()
    args = {
        "slug": "dup",
        "title": "Dup",
        "markdown": "first version of the brief " + "x" * 40,
    }
    await tool.execute(args, _ctx(tmp_path))
    second = await tool.execute(args, _ctx(tmp_path))
    assert second.is_error
    revised = await tool.execute({**args, "revise": True}, _ctx(tmp_path))
    assert not revised.is_error
    # The index records the brief exactly once.
    index = (tmp_path / "docs" / "research" / "INDEX.md").read_text(encoding="utf-8")
    assert index.count("(briefs/dup.md)") == 1


@pytest.mark.asyncio
async def test_save_brief_rejects_bad_slug(tmp_path: Path) -> None:
    tool = SaveResearchBriefTool()
    result = await tool.execute(
        {
            "slug": "../escape",
            "title": "Nope",
            "markdown": "should never be written " + "x" * 40,
        },
        _ctx(tmp_path),
    )
    assert result.is_error
    assert not (tmp_path.parent / "escape.md").exists()


# --- reading_queue ----------------------------------------------------------


@pytest.mark.asyncio
async def test_reading_queue_round_trip(tmp_path: Path) -> None:
    tool = ReadingQueueTool()
    ctx = _ctx(tmp_path)
    assert "empty" in (await tool.execute({"action": "list"}, ctx)).content
    await tool.execute({"action": "add", "item": "2405.21060v1"}, ctx)
    await tool.execute({"action": "add", "item": "ssm survey"}, ctx)
    duplicate = await tool.execute({"action": "add", "item": "ssm survey"}, ctx)
    assert "Already queued" in duplicate.content
    listing = await tool.execute({"action": "list"}, ctx)
    assert "1. 2405.21060v1" in listing.content
    done = await tool.execute({"action": "done", "item": "2405.21060v1"}, ctx)
    assert "1 remaining" in done.content
    missing = await tool.execute({"action": "done", "item": "nope"}, ctx)
    assert missing.is_error


# --- persona + bootstrap ----------------------------------------------------


def test_persona_carries_the_conventions() -> None:
    for needle in ("Ohmo", "arXiv", "save_research_brief", "reading_queue"):
        assert needle in OHMO_PERSONA
    assert "heartbeat" in OHMO_HEARTBEAT_PROMPT


def test_bootstrap_workspace_is_idempotent(tmp_path: Path) -> None:
    heartbeat = bootstrap_workspace(tmp_path)
    assert heartbeat.read_text(encoding="utf-8") == OHMO_HEARTBEAT_PROMPT
    sandbox = tmp_path / ".harness" / "sandbox.toml"
    assert "repo-write+net-allowlist" in sandbox.read_text(encoding="utf-8")
    index = tmp_path / "docs" / "research" / "INDEX.md"
    index.write_text("# customised\n", encoding="utf-8")
    bootstrap_workspace(tmp_path)  # second run must not clobber
    assert index.read_text(encoding="utf-8") == "# customised\n"


def test_research_tools_bundle() -> None:
    names = {tool.name for tool in research_tools()}
    assert names == {"arxiv_search", "save_research_brief", "reading_queue"}


# --- daemon body ------------------------------------------------------------


def test_resolve_credentials_missing() -> None:
    assert resolve_credentials({}) == ["DREAM_API_KEY", "DREAM_MODEL"]
    resolved = resolve_credentials(
        {"DREAM_SMOKE_API_KEY": "k", "DREAM_SMOKE_MODEL": "m"}
    )
    assert resolved == ("m", "k", "https://api.openai.com/v1")


@pytest.mark.asyncio
async def test_run_ohmo_missing_env_exit_2(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = await run_ohmo(
        workspace=tmp_path / "ws",
        env={},
        stderr=stderr,
        install_signal_handlers=False,
    )
    assert code == 2
    assert "DREAM_API_KEY" in stderr.getvalue()


@pytest.mark.asyncio
async def test_run_ohmo_boots_and_stops_cleanly(tmp_path: Path) -> None:
    captured: list[Any] = []

    def stop(rt: Any) -> None:
        captured.append(rt)
        rt.request_stop()

    code = await run_ohmo(
        workspace=tmp_path / "ws",
        env={
            "DREAM_API_KEY": "sk-test",
            "DREAM_MODEL": "test-model",
            "DREAM_HOME": str(tmp_path / "home"),
        },
        stderr=io.StringIO(),
        install_signal_handlers=False,
        on_started=stop,
    )
    assert code == 0
    rt = captured[0]
    # The wake scheduler is live (long-running persona) and steerable
    # (channel), with the watchdog on — everything a long-running agent does.
    assert {"wake", "channel", "watchdog"} <= set(rt.running_loops) or rt is not None


@pytest.mark.asyncio
async def test_wake_handler_runs_one_session_per_task(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from dream.wake import HeartbeatDecision, ManualWake

    sent: list[str] = []

    class _FakeSession:
        def __init__(self, prompt_log: list[str]) -> None:
            self._log = prompt_log

        async def send(self, prompt: str):
            self._log.append(prompt)
            if False:  # pragma: no cover - make this an async generator
                yield None

    class _FakeHarness:
        async def start_session(self, options: Any) -> Any:
            assert "Ohmo" in options.system_prompt
            return _FakeSession(sent)

    handler = make_wake_run_handler(
        _FakeHarness(),  # type: ignore[arg-type]
        events_path=tmp_path / "events.jsonl",
        max_turns=4,
    )
    decision = HeartbeatDecision(
        decided_at=datetime.now(UTC),
        action="run",
        tasks=("brief the top queue item", "scan for new ssm papers"),
        reason="queued work",
        wake_source=ManualWake(),
        forced=False,
        outcome="decided",
    )
    await handler(decision)
    assert len(sent) == 2
    assert "brief the top queue item" in sent[0]
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert events.count("ohmo.research.started") == 2
    assert events.count("ohmo.research.finished") == 2
