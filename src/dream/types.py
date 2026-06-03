"""Public type aliases shared across the surface."""

from __future__ import annotations

from typing import Literal

StopReason = Literal[
    "end_turn",
    "tool_use",
    "max_tokens",
    "stop_sequence",
    "cancelled",
    "error",
]

MessageRole = Literal["system", "user", "assistant", "tool"]


__all__ = ["MessageRole", "StopReason"]
