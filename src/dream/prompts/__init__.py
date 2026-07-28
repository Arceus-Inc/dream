"""System prompt assembly.

Blocks that compose a session's system prompt. ``environment`` renders the
host runtime facts (shell/OS/python) the model must trust when emitting
``task_create command=...`` calls. ``employee_base`` is the Hermes-style
stable workforce identity for org employees.
"""

from dream.prompts.employee_base import (
    EMPLOYEE_BASE_PROMPT,
    EMPLOYEE_MODE_METADATA_KEY,
    RECALL_DIRECTIVE,
    RESUME_DIRECTIVE,
    TOOL_CHOICE_MATRIX,
    render_employee_base_prompt,
    should_inject_employee_base,
)
from dream.prompts.environment import detect_shell, render_runtime_info
from dream.prompts.system_prompt import assemble_session_system_prompt

__all__ = [
    "EMPLOYEE_BASE_PROMPT",
    "EMPLOYEE_MODE_METADATA_KEY",
    "RECALL_DIRECTIVE",
    "RESUME_DIRECTIVE",
    "TOOL_CHOICE_MATRIX",
    "assemble_session_system_prompt",
    "detect_shell",
    "render_employee_base_prompt",
    "render_runtime_info",
    "should_inject_employee_base",
]
