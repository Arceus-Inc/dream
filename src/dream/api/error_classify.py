"""Transport / provider failure classification for the retries stack (Spec 02 + Hermes).

Maps an exception into a frozen :class:`ClassifiedFailure` with action hints so
:class:`~dream.engine.FailoverStreamer` does not re-decide policy from raw
status codes. Overflow stays a compaction problem — never failover.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

import httpx

from dream.api.credentials import AttemptOutcome
from dream.services.compact._overflow import is_context_length_overflow

# Cap provider-requested waits so a multi-hour Retry-After cannot stall failover
# (OpenClaw ``OPENCLAW_SDK_RETRY_MAX_WAIT_SECONDS`` pattern).
MAX_RETRY_AFTER_SECONDS: Final[float] = 60.0


class FailureKind(Enum):
    """Closed set of transport-layer failure classes."""

    RATE_LIMIT = "rate_limit"
    SERVER_TRANSIENT = "server_transient"
    TRANSPORT = "transport"
    AUTH = "auth"
    CONTEXT_OVERFLOW = "context_overflow"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    HARD = "hard"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedFailure:
    """Action hints for one failed stream attempt — never carries secret material."""

    kind: FailureKind
    outcome: AttemptOutcome
    retryable: bool
    should_failover: bool
    should_compress: bool
    backoff_seconds: float | None = None


def classify_failure(exc: BaseException) -> ClassifiedFailure:
    """Classify ``exc`` for pool recording + streamer control flow."""
    if isinstance(exc, httpx.TransportError):
        return ClassifiedFailure(
            kind=FailureKind.TRANSPORT,
            outcome=AttemptOutcome.TRANSIENT_EXHAUSTED,
            retryable=True,
            should_failover=True,
            should_compress=False,
        )

    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http(exc)

    return ClassifiedFailure(
        kind=FailureKind.UNKNOWN,
        outcome=AttemptOutcome.HARD_REFUSAL,
        retryable=False,
        should_failover=False,
        should_compress=False,
    )


def _classify_http(exc: httpx.HTTPStatusError) -> ClassifiedFailure:
    status = exc.response.status_code

    if is_context_length_overflow(exc):
        return ClassifiedFailure(
            kind=FailureKind.CONTEXT_OVERFLOW,
            outcome=AttemptOutcome.HARD_REFUSAL,
            retryable=False,
            should_failover=False,
            should_compress=True,
        )

    if status == 413:
        return ClassifiedFailure(
            kind=FailureKind.PAYLOAD_TOO_LARGE,
            outcome=AttemptOutcome.HARD_REFUSAL,
            retryable=False,
            should_failover=False,
            should_compress=True,
        )

    if status in {401, 403}:
        return ClassifiedFailure(
            kind=FailureKind.AUTH,
            outcome=AttemptOutcome.AUTH,
            retryable=False,
            should_failover=True,
            should_compress=False,
        )

    if status == 429:
        return ClassifiedFailure(
            kind=FailureKind.RATE_LIMIT,
            outcome=AttemptOutcome.TRANSIENT_EXHAUSTED,
            retryable=True,
            should_failover=True,
            should_compress=False,
            backoff_seconds=_retry_after_seconds(exc.response),
        )

    if status in {408, 500, 502, 503, 504}:
        return ClassifiedFailure(
            kind=FailureKind.SERVER_TRANSIENT,
            outcome=AttemptOutcome.TRANSIENT_EXHAUSTED,
            retryable=True,
            should_failover=True,
            should_compress=False,
        )

    return ClassifiedFailure(
        kind=FailureKind.HARD,
        outcome=AttemptOutcome.HARD_REFUSAL,
        retryable=False,
        should_failover=False,
        should_compress=False,
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


__all__ = [
    "MAX_RETRY_AFTER_SECONDS",
    "ClassifiedFailure",
    "FailureKind",
    "classify_failure",
]
