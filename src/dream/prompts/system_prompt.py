"""Assemble the final system prompt from ordered blocks (Hermes-aligned).

Order mirrors Hermes stable → context → volatile within Dream's session
waist: standing orders, workforce Base Prompt (optional), runtime info,
catalogues, then the caller/role system prompt (craft brief + dream role).
"""

from __future__ import annotations

from dream.prompts.employee_base import render_employee_base_prompt, should_inject_employee_base
from dream.services.core_beliefs import extract_standing_orders, render_standing_orders


def assemble_session_system_prompt(
    *,
    standing_orders_path,
    runtime_info: str,
    catalogue: str,
    memory_catalogue: str,
    system_prompt: str | None,
    employee_mode: bool = False,
    metadata: dict[str, object] | None = None,
    tool_names: frozenset[str] | None = None,
    is_subagent: bool = False,
) -> str:
    """Join prompt blocks; empty blocks are omitted."""
    standing_orders = render_standing_orders(extract_standing_orders(standing_orders_path))
    parts: list[str] = []
    if standing_orders:
        parts.append(standing_orders)
    if should_inject_employee_base(
        employee_mode=employee_mode,
        metadata=metadata,
        system_prompt=system_prompt,
        is_subagent=is_subagent,
    ):
        parts.append(render_employee_base_prompt(tool_names=tool_names or frozenset()))
    parts.append(runtime_info)
    if catalogue:
        parts.append(catalogue)
    if memory_catalogue:
        parts.append(memory_catalogue)
    if system_prompt:
        parts.append(system_prompt)
    return "\n\n".join(parts)


__all__ = ["assemble_session_system_prompt"]
