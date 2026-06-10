"""digest — the rolling self-evolution AI digest agent (clock-driven example).

Covers the agent's own logic offline: HN feed parsing + hour-window query,
file delivery to research_ideas/{stamp}.md, workspace bootstrap (cron
manifest + trust promotions), the fire-now backdating, and the cron argv
payload. The runtime cron loop itself is covered by tests/test_runtime +
tests/test_services.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

from digest.agent import (  # noqa: E402
    CRON_JOB_NAME,
    bootstrap_workspace,
    fire_now,
    make_cron_argv_builder,
    parse_args,
    run_digest_once,
    run_stamp,
)
from digest.persona import DEFAULT_TOPIC, DIGEST_PERSONA, digest_instruction  # noqa: E402
from digest.tools import (  # noqa: E402
    HnSearchTool,
    SaveDigestTool,
    parse_hn_hits,
)

from dream.tasks._cron import (  # noqa: E402
    CronManifest,
    load_cron_jobs,
    load_cron_manifest,
    save_cron_jobs,
)
from dream.tools._context import ToolExecutionContext  # noqa: E402

_HN_BODY = json.dumps(
    {
        "hits": [
            {
                "title": "Self-evolving agents rewrite their own scaffolding",
                "url": "https://example.com/post",
                "points": 142,
                "num_comments": 87,
                "created_at": "2026-06-10T13:00:00Z",
                "objectID": "1",
            },
            {"objectID": "2"},  # malformed: no title — skipped
            {
                "title": "Ask HN: recursive self-improvement?",
                "url": None,
                "points": 10,
                "num_comments": 4,
                "created_at": "2026-06-10T13:30:00Z",
                "objectID": "333",
            },
        ]
    }
)


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s-test")


# --- HN search --------------------------------------------------------------


def test_parse_hn_hits() -> None:
    hits = parse_hn_hits(_HN_BODY)
    assert len(hits) == 2
    assert hits[0].points == 142
    assert hits[1].url == "https://news.ycombinator.com/item?id=333"
    assert parse_hn_hits("{not json") == ()


@pytest.mark.asyncio
async def test_hn_search_uses_hour_window(tmp_path: Path) -> None:
    seen: list[str] = []

    async def fake_fetch(url: str) -> str:
        seen.append(url)
        return _HN_BODY

    tool = HnSearchTool(fetch=fake_fetch)
    result = await tool.execute(
        {"query": "self-evolving agents", "hours": 2}, _ctx(tmp_path)
    )
    assert not result.is_error
    assert "142 points" in result.content
    assert "hn.algolia.com" in seen[0]
    assert "created_at_i>" in seen[0]


@pytest.mark.asyncio
async def test_hn_search_failure_keeps_recovery_contract(tmp_path: Path) -> None:
    async def broken(url: str) -> str:
        raise OSError("down")

    result = await HnSearchTool(fetch=broken).execute(
        {"query": "anything"}, _ctx(tmp_path)
    )
    assert result.is_error
    assert "stop_condition" in result.metadata


# --- file delivery ----------------------------------------------------------


def _digest_args() -> dict[str, Any]:
    return {
        "title": "Self-Evolution AI — 2026-06-10T14-30",
        "markdown": "## News & Discussion\n\n- A story about self-evolving "
        "agents that matters because it is interesting.\n",
    }


@pytest.mark.asyncio
async def test_save_digest_writes_timestamped_file(tmp_path: Path) -> None:
    tool = SaveDigestTool(stamp="2026-06-10T14-30")
    result = await tool.execute(_digest_args(), _ctx(tmp_path))
    assert not result.is_error
    out = tmp_path / "research_ideas" / "2026-06-10T14-30.md"
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "self-evolving" in body
    assert "Generated 2026-06-10T14-30" in body
    assert result.metadata["artifacts"] == ["research_ideas/2026-06-10T14-30.md"]


@pytest.mark.asyncio
async def test_save_digest_overwrites_within_same_run(tmp_path: Path) -> None:
    tool = SaveDigestTool(stamp="2026-06-10T14-30")
    await tool.execute(_digest_args(), _ctx(tmp_path))
    await tool.execute(
        {"title": "Revised — 2026-06-10T14-30", "markdown": "## Take\n\n" + "x" * 40},
        _ctx(tmp_path),
    )
    files = list((tmp_path / "research_ideas").glob("*.md"))
    assert len(files) == 1  # same stamp → one file


def test_run_stamp_is_filesystem_safe() -> None:
    stamp = run_stamp()
    assert ":" not in stamp and " " not in stamp
    assert len(stamp) == 16  # YYYY-MM-DDTHH-MM


# --- bootstrap + cron --------------------------------------------------------


def test_bootstrap_writes_two_hourly_manifest_and_promotions(tmp_path: Path) -> None:
    bootstrap_workspace(tmp_path)
    manifest = load_cron_manifest(
        tmp_path / ".harness" / "cron" / f"{CRON_JOB_NAME}.toml"
    )
    assert manifest.name == CRON_JOB_NAME
    assert manifest.schedule == "0 */2 * * *"
    overrides = (tmp_path / ".harness" / "tool-tier-overrides.toml").read_text(
        encoding="utf-8"
    )
    # All three per-repo tools must be promoted — save_digest is mutating
    # and is denied (forcing a bash fallback) if it stays read-only.
    for name in ("arxiv_search", "hn_search", "save_digest"):
        assert f"[{name}]" in overrides
    assert (tmp_path / "research_ideas").is_dir()


def test_cron_argv_builder_targets_once_mode(tmp_path: Path) -> None:
    argv_for = make_cron_argv_builder(
        workspace=tmp_path, topic="self-evolution", window_hours=2
    )
    argv = argv_for(CronManifest(name=CRON_JOB_NAME, schedule="0 */2 * * *"))
    assert "--once" in argv
    assert "--window-hours" in argv and "2" in argv
    assert str(tmp_path) in argv
    assert argv[1].endswith("agent.py")
    # Foreign manifests keep the visible-firing stub.
    other = argv_for(CronManifest(name="dream-phase", schedule="0 3 * * *"))
    assert "--once" not in other


def test_fire_now_backdates_only_the_digest_job(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    future = datetime.now(UTC) + timedelta(hours=5)
    jobs = [
        CronManifest(name=CRON_JOB_NAME, schedule="0 */2 * * *"),
        CronManifest(name="other", schedule="0 3 * * *"),
    ]
    from dream.tasks._cron import CronJob

    save_cron_jobs(
        registry,
        [
            CronJob(name=j.name, schedule=j.schedule, enabled=True, next_run=future)
            for j in jobs
        ],
    )
    fire_now(registry)
    by_name = {j.name: j for j in load_cron_jobs(registry)}
    assert by_name[CRON_JOB_NAME].next_run is not None
    assert by_name[CRON_JOB_NAME].next_run < datetime.now(UTC) + timedelta(minutes=1)
    assert by_name["other"].next_run == future  # untouched


# --- args + persona -----------------------------------------------------------


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.window_hours == 2
    assert not args.once
    assert "self-evolving" in args.topic


def test_persona_and_instruction() -> None:
    assert "save_digest" in DIGEST_PERSONA
    text = digest_instruction(
        topic=DEFAULT_TOPIC, window_hours=2, stamp="2026-06-10T14-30"
    )
    assert "last 2 hours" in text
    assert "2026-06-10T14-30" in text


@pytest.mark.asyncio
async def test_run_once_missing_env_exit_2(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = await run_digest_once(workspace=tmp_path / "ws", env={}, stderr=stderr)
    assert code == 2
    assert "DREAM_API_KEY" in stderr.getvalue()
