"""CodeAnt #18 lock-in — bash read-only classification for git.

Treating ANY command starting with ``git`` as read-only downclassifies
mutating git operations (commit / reset --hard / clean) as safe. Only a
vetted git read-only subcommand set may be downclassified.
"""

from __future__ import annotations

import pytest

from dream.tools.builtin.bash import BashTool


@pytest.fixture
def tool() -> BashTool:
    return BashTool()


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git diff HEAD~1",
        "git log --oneline",
        "git show HEAD",
        "git rev-parse HEAD",
        "git ls-files",
        "git blame README.md",
    ],
)
def test_git_read_only_subcommands_classified_read_only(tool: BashTool, command: str) -> None:
    assert tool.is_read_only_for({"command": command}) is True


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m wip",
        "git reset --hard",
        "git clean -fd",
        "git checkout main",
        "git push origin main",
        "git merge feature",
        "git rebase main",
        "git pull",
        "git branch new-branch",
        "git",  # bare git
    ],
)
def test_git_mutating_commands_not_read_only(tool: BashTool, command: str) -> None:
    assert tool.is_read_only_for({"command": command}) is False
