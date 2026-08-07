"""Unit + table tests for transport failure classification."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from dream.api.credentials import AttemptOutcome
from dream.api.error_classify import (
    MAX_RETRY_AFTER_SECONDS,
    FailureKind,
    classify_failure,
)


def _status(
    code: int, *, body: bytes = b"{}", retry_after: str | None = None
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    headers = {"Retry-After": retry_after} if retry_after is not None else None
    response = httpx.Response(code, request=request, content=body, headers=headers)
    return httpx.HTTPStatusError(f"{code}", request=request, response=response)


@pytest.mark.parametrize(
    ("exc", "kind", "retryable", "failover", "compress", "outcome"),
    [
        (
            httpx.ConnectError("down"),
            FailureKind.TRANSPORT,
            True,
            True,
            False,
            AttemptOutcome.TRANSIENT_EXHAUSTED,
        ),
        (
            _status(402),
            FailureKind.BILLING,
            False,
            True,
            False,
            AttemptOutcome.AUTH,
        ),
        (
            _status(404, body=b'{"error":{"message":"The model `foo` does not exist"}}'),
            FailureKind.MODEL_NOT_FOUND,
            False,
            True,
            False,
            AttemptOutcome.HARD_REFUSAL,
        ),
        (
            _status(429),
            FailureKind.RATE_LIMIT,
            True,
            True,
            False,
            AttemptOutcome.TRANSIENT_EXHAUSTED,
        ),
        (
            _status(503),
            FailureKind.SERVER_TRANSIENT,
            True,
            True,
            False,
            AttemptOutcome.TRANSIENT_EXHAUSTED,
        ),
        (
            _status(401),
            FailureKind.AUTH,
            False,
            True,
            False,
            AttemptOutcome.AUTH,
        ),
        (
            _status(400),
            FailureKind.HARD,
            False,
            False,
            False,
            AttemptOutcome.HARD_REFUSAL,
        ),
        (
            _status(413),
            FailureKind.CONTEXT_OVERFLOW,
            False,
            False,
            True,
            AttemptOutcome.HARD_REFUSAL,
        ),
        (
            _status(
                400,
                body=b'{"error":{"code":"context_length_exceeded","message":"too long"}}',
            ),
            FailureKind.CONTEXT_OVERFLOW,
            False,
            False,
            True,
            AttemptOutcome.HARD_REFUSAL,
        ),
    ],
)
def test_classify_table(
    exc: BaseException,
    kind: FailureKind,
    retryable: bool,
    failover: bool,
    compress: bool,
    outcome: AttemptOutcome,
) -> None:
    got = classify_failure(exc)
    assert got.kind is kind
    assert got.retryable is retryable
    assert got.should_failover is failover
    assert got.should_compress is compress
    assert got.outcome == outcome


def test_retry_after_capped_at_max() -> None:
    got = classify_failure(_status(429, retry_after="99999"))
    assert got.backoff_seconds == MAX_RETRY_AFTER_SECONDS


def test_retry_after_missing_is_none() -> None:
    got = classify_failure(_status(429))
    assert got.backoff_seconds is None


def test_retry_after_http_date_is_parsed() -> None:
    from datetime import timedelta
    from email.utils import format_datetime

    when = datetime.now(UTC) + timedelta(seconds=45)
    got = classify_failure(_status(429, retry_after=format_datetime(when, usegmt=True)))
    assert got.backoff_seconds is not None
    assert 0 < got.backoff_seconds <= MAX_RETRY_AFTER_SECONDS


def test_retry_after_past_http_date_is_none() -> None:
    from datetime import timedelta
    from email.utils import format_datetime

    when = datetime.now(UTC) - timedelta(seconds=30)
    got = classify_failure(_status(429, retry_after=format_datetime(when, usegmt=True)))
    assert got.backoff_seconds is None


def test_provider_error_is_retryable_transport() -> None:
    from dream.errors import ProviderError

    got = classify_failure(ProviderError("malformed SSE", code="dream.provider.malformed_stream"))
    assert got.kind is FailureKind.TRANSPORT
    assert got.retryable is True
    assert got.should_failover is True


def test_unknown_exception_is_hard() -> None:
    got = classify_failure(RuntimeError("nope"))
    assert got.kind is FailureKind.UNKNOWN
    assert got.retryable is False
    assert got.should_failover is False
