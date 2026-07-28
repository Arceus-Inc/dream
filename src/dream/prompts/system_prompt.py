"""Assemble the final system prompt from ordered blocks.

Standing orders come from ``core-beliefs.md`` (workforce waist + governance).
Caller/role system prompt carries dream role + craft brief.
"""

from __future__ import annotations

from pathlib import Path

from dream.services.core_beliefs import extract_standing_orders, render_standing_orders


def assemble_session_system_prompt(
    *,
    standing_orders_path: Path,
    runtime_info: str,
    catalogue: str,
    memory_catalogue: str,
    system_prompt: str | None,
) -> str:
    """Join prompt blocks; empty blocks are omitted."""
    standing_orders = render_standing_orders(extract_standing_orders(standing_orders_path))
    parts: list[str] = []
    if standing_orders:
        parts.append(standing_orders)
    parts.append(runtime_info)
    if catalogue:
        parts.append(catalogue)
    if memory_catalogue:
        parts.append(memory_catalogue)
    if system_prompt:
        parts.append(system_prompt)
    return "\n\n".join(parts)


__all__ = ["assemble_session_system_prompt"]
