"""The long-running construct (spec 15 P1).

``Runtime`` composes the daemon-shaped machinery that already exists in
pieces — cron tick loop, wake-cycle heartbeat, BackgroundTaskManager,
boot gates, sidecar resume scan — into one supervised, observable,
single-instance process::

    harness = dream.build_harness(model=..., api_key=..., working_dir=...)
    async with dream.Runtime(harness) as rt:
        await rt.run_forever()        # days, not minutes

Deterministic supervisor loops outside, LLM only inside turns. The REPL
(and any other frontend) is a *client* of this runtime, never the owner
of its composition.
"""

from __future__ import annotations

from dream.runtime._boot import BootReport, run_boot_gates, scan_resume_candidates
from dream.runtime._runtime import (
    Runtime,
    RuntimeBootBlockedError,
    RuntimeBusyError,
    RuntimeConfig,
)
from dream.runtime._supervisor import supervise_loop
from dream.runtime._wake_scheduler import wake_scheduler_loop

__all__ = [
    "BootReport",
    "Runtime",
    "RuntimeBootBlockedError",
    "RuntimeBusyError",
    "RuntimeConfig",
    "run_boot_gates",
    "scan_resume_candidates",
    "supervise_loop",
    "wake_scheduler_loop",
]
