"""System prompt assembly.

Blocks that compose a session's system prompt. ``environment`` renders the
host runtime facts (shell/OS/python) the model must trust when emitting
``task_create command=...`` calls.
"""

from dream.prompts.environment import detect_shell, render_runtime_info

__all__ = ["detect_shell", "render_runtime_info"]
