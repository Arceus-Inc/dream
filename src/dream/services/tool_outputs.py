"""Spill large tool outputs to disk, return file refs (Spec 04 stage 4a).

Two responsibilities:

- The microcompact eligibility predicate (``is_microcompactable_tool_result``)
  — tells the compactor which old ``ToolResultBlock`` contents are safe to
  drop without breaking the tool-call atom (Spec 00 invariant #1; the
  structural ``ToolResultBlock`` shell stays, only its content goes).
- The offload mechanism: ``offload_tool_output`` writes oversized results to
  sidecar scratch and returns a typed ``OffloadPointer`` plus a short inline
  preview; ``read_offloaded`` slices the artifact on demand.

Inline/preview/microcompact thresholds are re-read from environment each
call so an operator can tune them without restarting the harness.
Defaults come from new-spec 04 (4 KB inline limit).

Borrowed shape from OpenHarness's ``services/tool_outputs.py`` +
``engine/query.py::_offload_tool_output_if_needed``; the typed
``OffloadPointer`` (vs. a bare ``Path``) and ``read_offloaded`` are
dream-specific so the act-loop can branch on a structured pointer.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from dream.utils.fs import atomic_write_text

# Spec 04 acceptance #11 + new-spec 04 conceptual default of 4 KB inline limit.
DEFAULT_TOOL_OUTPUT_INLINE_CHARS: int = 4_000
DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS: int = 800
DEFAULT_MICROCOMPACT_TOOL_RESULT_CHARS: int = 4_000


@dataclass(frozen=True)
class OffloadPointer:
    """Typed reference to an offloaded tool result on disk."""

    offloaded_to: str  # relative path under ``scratch_dir``
    original_size_bytes: int
    head_chars: int  # how much of the head landed in the inline preview
    tail_chars: int  # mirror for tail (0 if head-only)
    summary: str  # one-line description for context


# --- env-driven knobs --------------------------------------------------------


def _read_positive_int_env(name: str, default: int, *, minimum: int) -> int:
    # Garbage env values silently fall back to the default — spec 00 rule 4 bans
    # logging in src/, and the caller has no actionable response to a typo.
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def tool_output_inline_chars() -> int:
    """Per-tool inline budget. Env: ``DREAM_TOOL_OUTPUT_INLINE_CHARS``."""
    return _read_positive_int_env(
        "DREAM_TOOL_OUTPUT_INLINE_CHARS",
        DEFAULT_TOOL_OUTPUT_INLINE_CHARS,
        minimum=256,
    )


def tool_output_preview_chars() -> int:
    """Char count of the head preview surfaced inline for an offloaded result.

    Operators may legitimately want very small previews when debugging
    context bloat, so we only clamp at 1 — the inline-chars budget already
    bounds total spill behaviour.
    """
    return _read_positive_int_env(
        "DREAM_TOOL_OUTPUT_PREVIEW_CHARS",
        DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS,
        minimum=1,
    )


def microcompact_tool_result_chars() -> int:
    """Minimum size at which a non-MCP tool result becomes microcompactable."""
    return _read_positive_int_env(
        "DREAM_MICROCOMPACT_TOOL_RESULT_CHARS",
        DEFAULT_MICROCOMPACT_TOOL_RESULT_CHARS,
        minimum=256,
    )


# --- predicate ---------------------------------------------------------------


def is_microcompactable_tool_result(tool_name: str, content: str) -> bool:
    """True iff a settled old tool result is eligible for content-drop.

    MCP tools are coarse-grained adapters whose outputs are routinely large
    and routinely re-callable, so they're always eligible. Local tools are
    eligible only when their output is large enough that dropping it actually
    saves room.
    """
    normalized = tool_name.strip()
    if normalized.startswith("mcp__"):
        return True
    return len(content) >= microcompact_tool_result_chars()


# --- offload mechanism -------------------------------------------------------


def _safe_tool_artifact_name(tool_name: str) -> str:
    # First replace anything outside the safe alphabet, then collapse any
    # `..` sequences — dots are allowed in tool names (e.g. ``a.b.c``) but a
    # bare ``..`` would still walk the path on the disk side.
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name.strip())
    normalized = normalized.replace("..", "_")
    return (normalized or "tool")[:80]


def offload_tool_output(
    content: str,
    *,
    scratch_dir: Path,
    tool_use_id: str,
    tool_name: str,
    summary: str | None = None,
) -> tuple[str, OffloadPointer | None]:
    """Spill over-limit content to scratch; return ``(inline_text, pointer)``.

    Returns ``(content, None)`` when ``content`` fits the inline budget. The
    scratch directory is created on demand so callers don't have to pre-stage.
    """
    inline_limit = tool_output_inline_chars()
    if len(content) <= inline_limit:
        return content, None

    scratch_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{time.strftime('%Y%m%d-%H%M%S')}"
        f"-{_safe_tool_artifact_name(tool_name)}"
        f"-{uuid4().hex[:12]}.txt"
    )
    artifact_path = scratch_dir / filename
    atomic_write_text(artifact_path, content)

    preview_limit = tool_output_preview_chars()
    preview = content[:preview_limit]
    omitted = max(0, len(content) - len(preview))
    original_size_bytes = len(content.encode("utf-8"))
    inline = (
        "[Tool output truncated]\n"
        f"Tool: {tool_name}\n"
        f"Tool use id: {tool_use_id}\n"
        f"Original size: {original_size_bytes} bytes\n"
        f"Full output saved to: {filename}\n"
        f"Inline preview: first {len(preview)} chars"
    )
    if omitted:
        inline += f" ({omitted} chars omitted)"
    if preview:
        inline += f"\n\nPreview:\n{preview}"

    pointer = OffloadPointer(
        offloaded_to=filename,
        original_size_bytes=original_size_bytes,
        head_chars=len(preview),
        tail_chars=0,
        summary=summary or f"{tool_name} output ({original_size_bytes} bytes)",
    )
    return inline, pointer


def read_offloaded(
    path: Path,
    *,
    start: int = 0,
    end: int | None = None,
) -> str:
    """Read a char slice from an offloaded artifact.

    Rejects path-traversal attempts: any ``..`` segment in the input path
    raises ``ValueError`` before we touch the filesystem. ``end=None`` reads
    to end-of-file.
    """
    if ".." in path.parts:
        raise ValueError(f"path traversal not allowed: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if end is None:
        return text[start:]
    return text[start:end]


__all__ = [
    "DEFAULT_MICROCOMPACT_TOOL_RESULT_CHARS",
    "DEFAULT_TOOL_OUTPUT_INLINE_CHARS",
    "DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS",
    "OffloadPointer",
    "is_microcompactable_tool_result",
    "microcompact_tool_result_chars",
    "offload_tool_output",
    "read_offloaded",
    "tool_output_inline_chars",
    "tool_output_preview_chars",
]
