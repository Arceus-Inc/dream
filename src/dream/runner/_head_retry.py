"""Self-healing head retry helper: spec resilience Fix 1.

LLM completions are occasionally malformed. Rather than surfacing a
``*HeadParseError`` straight out of ``run_task`` and killing the whole
task, the four parse-strict heads (planner, evaluator, evaluator-propose,
generator-respond) use :func:`ask_until_parsed` to re-prompt with the
error and previous bad reply, giving the model a second chance to emit the
required envelope.

Design decisions:
- A fresh role session is opened per attempt (``run_role`` always starts a
  new session — that is inherent to the heads' ``ask`` callables).
- Only parse errors are retried; engine-layer errors
  (:class:`~dream.runner.RoleSessionError`) are never swallowed.
- The retry budget is small by default (``DEFAULT_RETRIES = 2``, i.e. three
  total attempts) to avoid burning token budget on a stuck model.
- Callers declare their parse error type explicitly via ``parse_error=``
  so the helper never needs to guess which exception subclass to catch.
- The ``on_retry`` callback lets each head emit a ``head.retry`` observer
  event, making recoveries visible rather than silent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

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
    ask: Callable[[str], Awaitable[Any]],
    parse: Callable[[str], T],
    *,
    prompt: str,
    parse_error: type[Exception],
    retries: int = DEFAULT_RETRIES,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    """Ask the model and parse, retrying with feedback on parse failures.

    Parameters
    ----------
    ask:
        Async callable that takes a prompt string and returns a result object
        with a ``.final_text`` attribute (the role session's text output).
    parse:
        Callable that takes the final text and returns the parsed result, or
        raises an instance of ``parse_error`` on malformed output.
    prompt:
        The original prompt to send on the first attempt.
    parse_error:
        The exception *type* that signals a parse failure (and should trigger
        a retry).  Engine-layer errors (e.g. ``RoleSessionError``) are a
        *different* type and propagate immediately without retry.
    retries:
        How many additional attempts are allowed after the first failure.
        Total attempts = ``retries + 1``.  Defaults to
        :data:`DEFAULT_RETRIES` (= 2, so three attempts in total).
    on_retry:
        Optional callback invoked before each retry with
        ``(attempt_number, parse_error_instance)``.  ``attempt_number`` is
        1-based.  Use this to emit observer events, log warnings, etc.

    Returns
    -------
    T
        The successfully parsed result.

    Raises
    ------
    parse_error
        The *last* parse error instance after all attempts are exhausted.
    Any other exception
        Propagates immediately without retry (engine / network failures).
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
                # Exhausted all retries — re-raise the last parse error.
                raise
            # Prepare the feedback prompt for the next attempt.
            attempt_number = attempt + 1
            if on_retry is not None:
                on_retry(attempt_number, exc)
            current_prompt = _build_feedback_prompt(
                original_prompt=prompt,
                error=exc,
                previous_text=final_text,
            )

    # Unreachable but keeps mypy happy.
    assert last_exc is not None
    raise last_exc  # pragma: no cover


def _build_feedback_prompt(
    *,
    original_prompt: str,
    error: Exception,
    previous_text: str,
) -> str:
    """Construct the retry prompt embedding the original prompt + error context.

    The structure mirrors the spec decision: original prompt first, then the
    error message, then the previous reply wrapped in ``<previous-reply>``
    tags, and a clear instruction to re-emit the complete, well-formed reply.
    """
    return (
        f"{original_prompt}"
        "\n\n"
        f"Your previous reply could not be used: {error}\n"
        "Your previous reply is below between <previous-reply> tags. "
        "Re-emit your COMPLETE reply with the required envelope(s), and nothing else.\n"
        "<previous-reply>\n"
        f"{previous_text}\n"
        "</previous-reply>"
    )
