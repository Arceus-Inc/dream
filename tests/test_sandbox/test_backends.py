"""Sandbox execution backends (spec 13B; spec 15 P4 §5).

Subprocess is the default v1 backend (the repo is the security
boundary); Docker is an upgrade path behind the same adapter Protocol —
a gated seam that refuses loudly until a real container backend lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dream.errors import SandboxError
from dream.sandbox import (
    DockerSandbox,
    SandboxAdapter,
    SubprocessSandbox,
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
async def test_docker_backend_refuses(tmp_path: Path) -> None:
    sandbox = DockerSandbox()
    with pytest.raises(SandboxError, match="docker"):
        await sandbox.run("echo hi", cwd=tmp_path)


def test_select_backend_defaults_to_subprocess() -> None:
    backend = select_backend("subprocess")
    assert isinstance(backend, SubprocessSandbox)
    assert isinstance(backend, SandboxAdapter)


def test_select_backend_docker_is_the_gated_seam() -> None:
    assert isinstance(select_backend("docker"), DockerSandbox)


def test_select_backend_unknown_refused() -> None:
    with pytest.raises(SandboxError, match="unknown sandbox backend"):
        select_backend("firecracker")
