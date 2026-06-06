"""Bundled default ``HEARTBEAT.md`` + operator override loader.

Spec 06.5: ``HEARTBEAT.md`` is the background-turn system prompt — and
only the background-turn system prompt. It is NOT mixed into the main
session's prompt stack.

The bundled default is shipped as a Python string constant so the package
has no resource-loading dependency; operators override by writing a real
``.dream/HEARTBEAT.md`` in the repo, which the loader returns verbatim
(no trim, no rewrap) when present.
"""

from __future__ import annotations

from pathlib import Path

BUNDLED_HEARTBEAT_PROMPT = """\
You are running a background WAKE-CYCLE turn.

Your ONLY job this turn is to decide whether to start work.

Call the `heartbeat` tool EXACTLY ONCE with:
  - action: "run" if there is queued work that is ready to execute now,
            "skip" if there is nothing ready or it is wiser to defer.
  - tasks:  when action="run", a list of up to 5 short task descriptions
            (each <=200 chars) you intend to execute this session.
            Ignored when action="skip".
  - reason: a single sentence explaining the decision (<=200 chars).

Do NOT call any other tool. Do NOT respond with prose only — the wake
runner reads your `heartbeat` tool call structurally and ignores prose.
"""


def load_heartbeat_prompt(path: Path | None) -> str:
    """Return the operator override at ``path`` or the bundled default.

    ``path=None`` or a path that does not exist falls back to the bundled
    default. When the file exists it is returned VERBATIM — no whitespace
    trimming, no rewrap — so operators retain byte-level control.
    """
    if path is None:
        return BUNDLED_HEARTBEAT_PROMPT
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return BUNDLED_HEARTBEAT_PROMPT


def forced_addendum(skip_streak: int) -> str:
    """Return the anti-coma addendum spliced into the wake stimulus when
    the agent's ``skip_streak`` has reached the configured maximum.

    The wording is deliberately concrete (mentions the streak count, that
    prior skips were *declined*, and that the model is now *forced* to
    pick at least one task) so the model has unambiguous in-context
    signal that this is not a normal wake turn.
    """
    return (
        f"\n\nANTI-COMA: your last {skip_streak} consecutive skip decisions "
        "have been declined. You are now in FORCED mode: 'skip' is not "
        "available this turn — you must choose at least one task or call "
        "the heartbeat tool with action='run' and tasks=[] to acknowledge "
        "the forced wake."
    )


__all__ = ["BUNDLED_HEARTBEAT_PROMPT", "forced_addendum", "load_heartbeat_prompt"]
