"""System prompt assembly.

Blocks that compose a session's system prompt. ``environment`` renders the
host runtime facts (shell/OS/python) the model must trust when emitting
``task_create command=...`` calls. Workforce invariants live in
``docs/design-docs/core-beliefs.md`` (Standing orders / What we don't do).
"""

from dream.prompts.environment import detect_shell, render_runtime_info
from dream.prompts.system_prompt import assemble_session_system_prompt

__all__ = [
    "assemble_session_system_prompt",
    "detect_shell",
    "render_runtime_info",
]
