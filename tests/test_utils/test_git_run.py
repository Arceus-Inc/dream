"""run_git — fixed argv wrapper; timeouts stay on the tuple contract."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from dream.utils import git as git_mod
from dream.utils.git import run_git


def test_run_git_timeout_returns_tuple_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=0.01, output="partial")

    monkeypatch.setattr(git_mod.subprocess, "run", boom)
    rc, stdout, stderr = run_git(["status"], cwd=tmp_path, timeout=0.01)
    assert rc == 124
    assert stdout == "partial"
    assert "timed out" in stderr
    assert "status" in stderr
