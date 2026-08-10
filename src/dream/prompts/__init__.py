"""System and first-user-context prompt assembly.

Blocks that compose a session's system prompt. ``environment`` renders the
host runtime facts (shell/OS/python) for the first user-context message.
"""

from dream.prompts.environment import detect_shell, render_runtime_info
from dream.prompts.system_prompt import (
    ContextPromptBlock,
    RolePromptBlock,
    RuntimeContextBlock,
    StablePromptBlock,
    assemble_session_system_prompt,
    load_agents_md,
    packaged_standing_orders,
)

__all__ = [
    "ContextPromptBlock",
    "RolePromptBlock",
    "RuntimeContextBlock",
    "StablePromptBlock",
    "assemble_session_system_prompt",
    "detect_shell",
    "load_agents_md",
    "packaged_standing_orders",
    "render_runtime_info",
]
