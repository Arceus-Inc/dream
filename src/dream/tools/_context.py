"""Concrete ``ToolExecutionContext`` — satisfies ``contracts.tool.ToolContext``.

Each ``BaseTool.execute`` call receives one of these. It carries the working
directory + session id + per-invocation cancellation flag, plus the two
side-effecting operations the engine wants tools to route through it:

- ``run_subprocess``: a *single auditable* subprocess wrapper. Spec 01
  invariant: only ``utils/git.py`` may import ``subprocess`` — for runtime
  process spawning we use ``asyncio`` instead, which the invariants do not
  restrict (they prohibit ``import subprocess`` outside ``utils.git``, not
  ``asyncio.create_subprocess_exec``).
- ``spill_large_output``: thin wrapper over
  ``dream.services.tool_outputs.offload_tool_output`` so tools don't have to
  thread sidecar paths themselves.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from dream.contracts.tool import ToolResult
from dream.services.tool_outputs import offload_tool_output


@dataclass
class ToolExecutionContext:
    """Per-invocation execution context.

    Mutable on ``cancel_requested`` only — the engine flips this when the
    caller cancels mid-turn and well-behaved tools poll it between steps.
    """

    working_dir: Path
    session_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    scratch_dir: Path | None = None
    cancel_requested: bool = False

    def request_cancel(self) -> None:
        """Flip the cancel flag. Tools cooperatively check it."""
        self.cancel_requested = True

    async def run_subprocess(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ToolResult:
        """Spawn ``argv`` and return its result as a ``ToolResult``.

        Stdout + stderr are concatenated into ``content``; ``metadata``
        carries ``returncode``, ``stdout_bytes``, ``stderr_bytes`` so the
        observation derivation does not need to parse the stream.
        """
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd or self.working_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                content=f"subprocess timed out after {timeout}s",
                is_error=True,
                metadata={
                    "argv": argv,
                    "timeout_seconds": timeout,
                    "root_cause": "subprocess exceeded timeout",
                    "safe_retry": "rerun with shorter scope or larger timeout",
                    "stop_condition": "do not retry beyond declared tool timeout",
                },
            )
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        is_error = proc.returncode != 0
        # Compose deterministic content: stdout, then a separator + stderr
        # if stderr produced anything. Avoids the case where stderr-only
        # output disappears.
        if stderr:
            content = f"{stdout}\n--- stderr ---\n{stderr}" if stdout else stderr
        else:
            content = stdout
        metadata: dict[str, Any] = {
            "returncode": proc.returncode,
            "stdout_bytes": len(stdout_b),
            "stderr_bytes": len(stderr_b),
        }
        if is_error:
            metadata.update(
                {
                    "root_cause": f"exit code {proc.returncode}",
                    "safe_retry": "inspect stderr and adjust arguments",
                    "stop_condition": "do not retry on the same arguments",
                }
            )
        return ToolResult(content=content, is_error=is_error, metadata=metadata)

    async def spill_large_output(self, content: str | bytes) -> str:
        """Spill ``content`` to scratch and return a reference token.

        Returns the offloaded filename (relative to ``scratch_dir``) when the
        payload exceeds the inline budget; returns the raw content otherwise
        (callers can rely on this to be the inline-safe text).
        """
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        scratch = self.scratch_dir
        if scratch is None:
            # No sidecar wired: the engine will mount one in slice D. For
            # slice A we fall through and let ``offload_tool_output`` create
            # ``<working_dir>/.dream/scratch`` on demand.
            scratch = self.working_dir / ".dream" / "scratch"
        inline, pointer = offload_tool_output(
            text,
            scratch_dir=scratch,
            tool_use_id=uuid4().hex[:12],
            tool_name="spill",
        )
        if pointer is None:
            return inline
        return pointer.offloaded_to


__all__ = ["ToolExecutionContext"]
