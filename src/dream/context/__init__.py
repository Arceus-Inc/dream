"""Session context-window observability (Cursor / Hermes-style pie)."""

from dream.context._breakdown import (
    ContextBreakdown,
    ContextCategory,
    compute_context_breakdown,
    format_context_breakdown,
    render_context_command,
)

__all__ = [
    "ContextBreakdown",
    "ContextCategory",
    "compute_context_breakdown",
    "format_context_breakdown",
    "render_context_command",
]
