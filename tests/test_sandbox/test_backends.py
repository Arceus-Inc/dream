"""Sandbox execution backends (spec 13B; spec 15 P4 §5).

Docker is the default backend; subprocess is an opt-in fallback behind
the same adapter Protocol.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from dream.errors import SandboxError
from dream.sandbox import (
    DockerSandbox,
    DockerSandboxConfig,
    SandboxAdapter,
    SubprocessSandbox,
    get_docker_availability,
    select_backend,
)


@pytest.mark.asyncio
async def test_subprocess_runs_command(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    result = await sandbox.run("echo hello", cwd=tmp_path)
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert not result.timed_out


@pytest.mark.asyncio
async def test_subprocess_captures_failure(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    result = await sandbox.run("echo bad >&2; exit 7", cwd=tmp_path)
    assert result.returncode == 7
    assert "bad" in result.stderr


@pytest.mark.asyncio
async def test_subprocess_timeout_kills_tree(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    result = await sandbox.run("sleep 60", cwd=tmp_path, timeout_seconds=0.2)
    assert result.timed_out
    assert result.returncode is None


@pytest.mark.asyncio
async def test_subprocess_env_is_explicit(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    result = await sandbox.run(
        f"{sys.executable} -c \"import os; print(os.environ.get('DREAM_X', 'unset'))\"",
        cwd=tmp_path,
        env={"DREAM_X": "42", "PATH": "/usr/bin:/bin"},
    )
    assert result.stdout.strip() == "42"


@pytest.mark.asyncio
async def test_docker_backend_refuses_when_docker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dream.sandbox.docker_backend.shutil.which", lambda name: None)
    sandbox = DockerSandbox()
    with pytest.raises(SandboxError, match="Docker CLI not found"):
        await sandbox.run("echo hi", cwd=tmp_path)


def test_select_backend_subprocess() -> None:
    backend = select_backend("subprocess")
    assert isinstance(backend, SubprocessSandbox)
    assert isinstance(backend, SandboxAdapter)


def test_select_backend_docker() -> None:
    assert isinstance(select_backend("docker"), DockerSandbox)


def test_select_backend_docker_honours_config() -> None:
    cfg = DockerSandboxConfig(image="custom:tag", cpu_limit=2.0, memory_limit="1g")
    backend = select_backend("docker", docker=cfg)
    assert isinstance(backend, DockerSandbox)
    assert backend.config.image == "custom:tag"
    assert backend.config.cpu_limit == 2.0


def test_select_backend_unknown_refused() -> None:
    with pytest.raises(SandboxError, match="unknown sandbox backend"):
        select_backend("firecracker")


def test_docker_availability_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dream.sandbox.docker_backend.shutil.which", lambda name: None)
    result = get_docker_availability()
    assert result.available is False
    assert "not found" in result.reason


def test_docker_availability_when_daemon_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    monkeypatch.setattr(
        "dream.sandbox.docker_backend.shutil.which",
        lambda name: "/usr/bin/docker",
    )
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.subprocess.run",
        MagicMock(side_effect=subprocess.CalledProcessError(1, "docker info")),
    )
    result = get_docker_availability()
    assert result.available is False
    assert "not running" in result.reason


def test_docker_availability_when_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.shutil.which",
        lambda name: "/usr/bin/docker",
    )
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.subprocess.run",
        MagicMock(return_value=MagicMock(returncode=0)),
    )
    result = get_docker_availability()
    assert result.available is True


def test_container_start_builds_correct_docker_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.shutil.which",
        lambda name: "/usr/bin/docker",
    )
    sandbox = DockerSandbox(
        config=DockerSandboxConfig(image="dream-sandbox:latest", cpu_limit=2.0, memory_limit="4g")
    )
    argv = sandbox._build_run_argv(Path("/repo"))

    assert argv[0] == "/usr/bin/docker"
    assert "run" in argv
    assert "--rm" in argv
    assert "--name" in argv
    name_idx = argv.index("--name")
    assert argv[name_idx + 1] == sandbox.container_name
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert argv[argv.index("--pids-limit") + 1] == "256"
    assert argv[argv.index("--cpus") + 1] == "2.0"
    assert argv[argv.index("--memory") + 1] == "4g"
    assert "tail" in argv
    assert "/dev/null" in argv
    resolved = str(Path("/repo").resolve())
    assert f"{resolved}:{resolved}" in argv


def test_resource_limits_omitted_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.shutil.which",
        lambda name: "/usr/bin/docker",
    )
    sandbox = DockerSandbox(config=DockerSandboxConfig(pids_limit=0))
    argv = sandbox._build_run_argv(Path("/repo"))
    assert "--cpus" not in argv
    assert "--memory" not in argv
    assert "--pids-limit" not in argv
    # Hardening flags remain even when resource limits are off.
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"


def test_relative_extra_mount_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.shutil.which",
        lambda name: "/usr/bin/docker",
    )
    sandbox = DockerSandbox(
        config=DockerSandboxConfig(extra_mounts=("relative/path:/mnt",))
    )
    with pytest.raises(SandboxError, match="absolute host path"):
        sandbox._build_run_argv(Path("/repo"))


def test_windows_native_platform_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dream.sandbox.docker_backend.platform.system", lambda: "Windows")
    monkeypatch.setattr("dream.sandbox.docker_backend.platform.release", lambda: "10")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    result = get_docker_availability()
    assert result.available is False
    assert "native Windows" in result.reason


@pytest.mark.asyncio
async def test_docker_run_delegates_to_docker_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.shutil.which",
        lambda name: "/usr/bin/docker",
    )
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.get_docker_availability",
        lambda: MagicMock(available=True, reason="", command="/usr/bin/docker"),
    )
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.ensure_image_available",
        AsyncMock(return_value=True),
    )

    captured: list[tuple] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.append(args)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
        mock_proc.pid = 12345
        return mock_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    sandbox = DockerSandbox()
    result = await sandbox.run("echo hello", cwd=tmp_path)

    assert result.returncode == 0
    assert "hello" in result.stdout
    # First call: docker run; second: docker exec
    assert len(captured) == 2
    run_argv = captured[0]
    exec_argv = captured[1]
    assert run_argv[1] == "run"
    assert exec_argv[1] == "exec"
    assert sandbox.container_name in exec_argv
    assert "bash" in exec_argv
    assert "-lc" in exec_argv
    assert "echo hello" in exec_argv
    await sandbox.stop()


@pytest.mark.asyncio
async def test_docker_stop_calls_docker_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dream.sandbox.docker_backend.shutil.which",
        lambda name: "/usr/bin/docker",
    )
    sandbox = DockerSandbox()
    sandbox._running = True

    captured: list[str] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.extend(args)
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        return mock_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await sandbox.stop()

    assert "stop" in captured
    assert sandbox.container_name in captured
    assert sandbox.is_running is False
