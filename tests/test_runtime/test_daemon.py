"""``python -m dream.daemon`` — headless entrypoint (spec 15 P1 §4).

env/config → ``build_harness`` → ``Runtime.run_forever()`` with graceful
signal handling. Exit codes mirror the REPL convention: 0 clean, 2 missing
credentials, 3 boot blocked, 4 instance already running.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from dream import Runtime
from dream.daemon import parse_args, run_daemon


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "DREAM_API_KEY": "sk-test",
        "DREAM_MODEL": "test-model",
        "DREAM_BASE_URL": "http://localhost:9",
        "DREAM_HOME": str(tmp_path / "home"),
    }


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.working_dir == Path.cwd()
    assert args.agent_id == "default"
    assert args.wake_idle_minutes is None


def test_parse_args_explicit() -> None:
    args = parse_args(
        ["--working-dir", "/tmp/x", "--agent-id", "emp-1", "--wake-idle-minutes", "30"]
    )
    assert args.working_dir == Path("/tmp/x")
    assert args.agent_id == "emp-1"
    assert args.wake_idle_minutes == 30


@pytest.mark.asyncio
async def test_missing_credentials_exit_2(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = await run_daemon(
        working_dir=tmp_path,
        env={},
        stderr=stderr,
        install_signal_handlers=False,
    )
    assert code == 2
    assert "DREAM_API_KEY" in stderr.getvalue()


@pytest.mark.asyncio
async def test_smoke_env_fallback_accepted(tmp_path: Path) -> None:
    # DREAM_SMOKE_* (the REPL contract) works as a fallback so existing
    # setups run the daemon unchanged. Boot then proceeds to a clean start.
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "DREAM_SMOKE_API_KEY": "sk-test",
        "DREAM_SMOKE_MODEL": "test-model",
        "DREAM_HOME": str(tmp_path / "home"),
    }

    def stop_immediately(rt: Runtime) -> None:
        rt.request_stop()

    code = await run_daemon(
        working_dir=repo,
        env=env,
        stderr=io.StringIO(),
        install_signal_handlers=False,
        on_started=stop_immediately,
    )
    assert code == 0


@pytest.mark.asyncio
async def test_boot_blocked_exit_3(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_aws = "AKIA" + "ABCDEFGHIJKLMNOP"
    (repo / ".env").write_text(f"AWS={fake_aws}\n", encoding="utf-8")
    stderr = io.StringIO()
    code = await run_daemon(
        working_dir=repo,
        env=_env(tmp_path),
        stderr=stderr,
        install_signal_handlers=False,
    )
    assert code == 3
    assert "blocked" in stderr.getvalue()


@pytest.mark.asyncio
async def test_second_instance_exit_4(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _env(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold(rt: Runtime) -> None:
        started.set()
        await release.wait()
        rt.request_stop()

    holder_tasks: list[asyncio.Task[None]] = []

    def on_started(rt: Runtime) -> None:
        holder_tasks.append(asyncio.create_task(hold(rt)))

    first = asyncio.create_task(
        run_daemon(
            working_dir=repo,
            env=env,
            stderr=io.StringIO(),
            install_signal_handlers=False,
            on_started=on_started,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    stderr = io.StringIO()
    code = await run_daemon(
        working_dir=repo,
        env=env,
        stderr=stderr,
        install_signal_handlers=False,
    )
    assert code == 4
    release.set()
    assert await asyncio.wait_for(first, timeout=5) == 0


@pytest.mark.asyncio
async def test_clean_run_exit_0_and_events_written(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    captured: list[Runtime] = []

    def on_started(rt: Runtime) -> None:
        captured.append(rt)
        rt.request_stop()

    code = await run_daemon(
        working_dir=repo,
        env=_env(tmp_path),
        stderr=io.StringIO(),
        install_signal_handlers=False,
        on_started=on_started,
    )
    assert code == 0
    assert captured and captured[0].events_path.exists()
