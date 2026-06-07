"""Tests for ``dream.repl._runtime_info`` — shell + OS detection in the system prompt.

The runtime-info block is injected at the top of the system prompt so the
model picks the correct ``task_create command=...`` syntax for the host
shell instead of guessing ``bash`` on Windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dream.repl._runtime_info import detect_shell, render_runtime_info


def test_detect_shell_uses_shell_env_on_posix(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert detect_shell({"SHELL": "/usr/bin/zsh"}) == "/usr/bin/zsh"


def test_detect_shell_falls_back_to_sh_on_posix(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert detect_shell({}) == "/bin/sh"


def test_detect_shell_uses_comspec_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert detect_shell({"COMSPEC": r"C:\Windows\System32\cmd.exe"}) == (
        r"C:\Windows\System32\cmd.exe"
    )


def test_detect_shell_falls_back_to_cmd_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert detect_shell({}) == "cmd.exe"


def test_render_runtime_info_names_block_and_shell(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    text = render_runtime_info(env={"SHELL": "/bin/bash"}, working_dir=tmp_path)
    # Header line so the model sees the block as authoritative grounding.
    assert text.startswith("Runtime environment\n")
    # Shell line names the exact subsystem affected (task_create command=...).
    assert "Shell (used by task_create command=...): /bin/bash" in text
    # The working_dir lands verbatim so the model doesn't guess relative paths.
    assert str(tmp_path) in text


def test_render_runtime_info_on_windows_picks_cmd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    text = render_runtime_info(env={"COMSPEC": "cmd.exe"}, working_dir=tmp_path)
    assert "Shell (used by task_create command=...): cmd.exe" in text
    # The instruction to switch syntax per shell is what stops Bug A — the
    # block is useless if it's silently dropped.
    assert "POSIX sh on Linux/macOS, cmd.exe on Windows" in text
