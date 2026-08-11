"""Session context-window observability."""

from dream.context._breakdown import (
    AdvertisedTool,
    ContextBreakdown,
    ContextCategory,
    PromptSurfaces,
    compute_context_breakdown,
    format_context_breakdown,
    render_context_command,
)

__all__ = [
    "AdvertisedTool",
    "ContextBreakdown",
    "ContextCategory",
    "PromptSurfaces",
    "compute_context_breakdown",
    "format_context_breakdown",
    "render_context_command",
]
