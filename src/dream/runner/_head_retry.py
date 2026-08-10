"""Self-healing head retry helper: spec resilience Fix 1.

Parse-strict heads (planner, evaluator) use :func:`ask_until_parsed` to
re-prompt on malformed JSON instead of killing the whole task.

Design decisions:
- Attempts share whatever session the head's ``ask`` callable opens. When
  ``session_reuse`` is true (head built with ``session_scope``), the retry
  prompt is a short correction only — the transcript already holds the
  original beat and the rejected reply.
- Only parse errors are retried; engine-layer errors
  (:class:`~dream.runner.RoleSessionError`) are never swallowed.
- The retry budget is small by default (``DEFAULT_RETRIES = 2``).
- ``on_retry`` lets each head emit a ``head.retry`` observer event.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar, runtime_checkable

__all__ = [
    "DEFAULT_RETRIES",
    "ask_until_parsed",
]

# Default retry budget: 2 retries = 3 total attempts.
DEFAULT_RETRIES: int = 2

T = TypeVar("T")


@runtime_checkable
class _HasFinalText(Protocol):
    """Minimal interface the helper requires from an ask() result."""

    @property
    def final_text(self) -> str: ...


async def ask_until_parsed(
    ask: Callable[[str], Awaitable[_HasFinalText]],
    parse: Callable[[str], T],
    *,
    prompt: str,
    parse_error: type[Exception],
    retries: int = DEFAULT_RETRIES,
    on_retry: Callable[[int, Exception], None] | None = None,
    session_reuse: bool = False,
) -> T:
    """Ask the model and parse, retrying with feedback on parse failures.

    When ``session_reuse`` is true, retries send a short correction (the
    shared session transcript already contains the original prompt and the
    rejected reply). Otherwise the full original prompt is re-sent with the
    error and previous reply quoted.
    """
    current_prompt = prompt
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        result = await ask(current_prompt)
        final_text: str = result.final_text

        try:
            return parse(final_text)
        except parse_error as exc:  # type: ignore[misc,unused-ignore]
            last_exc = exc
            if attempt >= retries:
                raise
            attempt_number = attempt + 1
            if on_retry is not None:
                on_retry(attempt_number, exc)
            current_prompt = _build_feedback_prompt(
                original_prompt=prompt,
                error=exc,
                previous_text=final_text,
                session_reuse=session_reuse,
            )

    assert last_exc is not None
    raise last_exc  # pragma: no cover


def _build_feedback_prompt(
    *,
    original_prompt: str,
    error: Exception,
    previous_text: str,
    session_reuse: bool,
) -> str:
    """Build a retry prompt that asks for JSON matching the response schema."""
    correction = (
        f"Your previous reply could not be used: {error}\n"
        "Re-emit your COMPLETE reply as JSON matching the response schema, "
        "and nothing else."
    )
    if session_reuse:
        return correction
    return (
        f"{original_prompt}"
        "\n\n"
        f"{correction}\n"
        "Your previous reply is below between <previous-reply> tags.\n"
        "<previous-reply>\n"
        f"{previous_text}\n"
        "</previous-reply>"
    )
