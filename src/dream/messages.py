"""Public conversation message types for resume / transcript seams.

These are the typed blocks a caller passes as ``resume_messages`` into
:meth:`Harness.start_session`, :meth:`Harness.run_role`, and
:meth:`Harness.run_task`. The engine sanitizes them on entry (tool-call
atom enforcement) before the model sees them.
"""

from __future__ import annotations

from dream.engine._messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    "ConversationMessage",
    "ImageBlock",
    "TextBlock",
    "ToolResultBlock",
    "ToolUseBlock",
]
