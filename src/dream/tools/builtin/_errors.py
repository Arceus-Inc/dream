"""Shared structured-error envelope for built-in tools (Spec 05 contract).

Every built-in tool reports a recoverable failure as a ``ToolResult`` carrying
the three-part error contract in ``metadata`` (``root_cause`` / ``safe_retry`` /
``stop_condition``) so ``derive_observation`` lifts them into
``Observation.next_actions`` without parsing prose. This was copy-pasted as an
identical ``_err`` helper in ~14 tool files; it now lives here once.
"""

from __future__ import annotations

from dream.contracts.tool import ToolResult


def tool_error(
    content: str,
    *,
    root_cause: str,
    safe_retry: str,
    stop_condition: str,
) -> ToolResult:
    """Build an ``is_error`` ``ToolResult`` with the Spec 05 three-part contract.

    ``content`` is the human/model-facing message; ``root_cause`` /
    ``safe_retry`` / ``stop_condition`` populate ``metadata`` so the engine can
    surface structured recovery guidance.
    """
    return ToolResult(
        content=content,
        is_error=True,
        metadata={
            "root_cause": root_cause,
            "safe_retry": safe_retry,
            "stop_condition": stop_condition,
        },
    )


__all__ = ["tool_error"]
