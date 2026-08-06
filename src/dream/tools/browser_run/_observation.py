"""Harness observation helpers for browser_run outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from dream.tools.browser_run._types import BrowserRunOutcome, BrowserRunStatus


@dataclass(frozen=True)
class RecoveryAdvice:
    """The spec 05 three-part recovery contract for one status."""

    root_cause: str
    safe_retry: str
    stop_condition: str


_RECOVERY: dict[BrowserRunStatus, RecoveryAdvice] = {
    BrowserRunStatus.SETUP_REQUIRED: RecoveryAdvice(
        root_cause="Chromium CDP endpoint unreachable or remote debugging not enabled",
        safe_retry=(
            "Ensure Chromium is running with --remote-debugging-port and "
            "DREAM_CHROMIUM_CDP_URL points at it, then retry once"
        ),
        stop_condition="do not loop more than twice without ops confirming the sidecar is up",
    ),
    BrowserRunStatus.TIMEOUT: RecoveryAdvice(
        root_cause="browser_run exceeded timeout",
        safe_retry="Narrow the script (one navigation + page_info) or raise timeout_seconds",
        stop_condition="do not retry the same long script unchanged",
    ),
    BrowserRunStatus.CANCELLED: RecoveryAdvice(
        root_cause="browser_run cancelled by caller",
        safe_retry="Re-issue when the beat is not cancelled",
        stop_condition="do not retry while cancel is requested",
    ),
    BrowserRunStatus.REFUSED: RecoveryAdvice(
        root_cause="browser_run refused before spawn",
        safe_retry="Fix config (binary / DREAM_CHROMIUM_CDP_URL) or remove cloud helpers from code",
        stop_condition="do not retry until the refusal reason is resolved",
    ),
    BrowserRunStatus.SCRIPT_ERROR: RecoveryAdvice(
        root_cause="browser-harness script exited non-zero",
        safe_retry="Inspect stderr traceback; fix helpers / selectors; retry with a smaller step",
        stop_condition="do not retry the identical failing script",
    ),
}

_SUCCESS_ADVICE = RecoveryAdvice(
    root_cause="browser_run completed",
    safe_retry="Continue with the next research or craft step",
    stop_condition="stop when the question is answered with cited URLs",
)


def recovery_advice_for(status: BrowserRunStatus) -> RecoveryAdvice:
    """The spec 05 recovery contract for a status (success advice for ``SUCCESS``)."""
    return _RECOVERY.get(status, _SUCCESS_ADVICE)


def summary_for(outcome: BrowserRunOutcome) -> str:
    """One-line summary for ToolResult.metadata['summary']."""
    kind = outcome.browser_kind.value
    verb = "ok" if outcome.status is BrowserRunStatus.SUCCESS else outcome.status.value
    url = outcome.url or (outcome.page or {}).get("url")
    base = f"browser_run {verb} · {kind}"
    return f"{base} · {url}" if url else base


__all__ = ["RecoveryAdvice", "recovery_advice_for", "summary_for"]
