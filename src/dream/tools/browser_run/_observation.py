"""Harness observation helpers for browser_run outcomes."""

from __future__ import annotations

from dream.tools.browser_run._types import BrowserRunOutcome, BrowserRunStatus


def summary_for(outcome: BrowserRunOutcome) -> str:
    """One-line summary for ToolResult.metadata['summary']."""
    status = outcome.status.value
    kind = outcome.browser_kind.value
    url = outcome.url or (outcome.page or {}).get("url")
    if outcome.status is BrowserRunStatus.SUCCESS:
        if url:
            return f"browser_run ok · {kind} · {url}"
        return f"browser_run ok · {kind}"
    if url:
        return f"browser_run {status} · {kind} · {url}"
    return f"browser_run {status} · {kind}"


def next_actions_for(status: BrowserRunStatus) -> tuple[str, str, str]:
    """Spec 05 three-part recovery contract."""
    if status is BrowserRunStatus.SETUP_REQUIRED:
        return (
            "Chromium CDP endpoint unreachable or remote debugging not enabled",
            "Ensure Chromium is running with --remote-debugging-port and "
            "DREAM_CHROMIUM_CDP_URL points at it, then retry once",
            "do not loop more than twice without ops confirming the sidecar is up",
        )
    if status is BrowserRunStatus.TIMEOUT:
        return (
            "browser_run exceeded timeout",
            "Narrow the script (one navigation + page_info) or raise timeout_seconds",
            "do not retry the same long script unchanged",
        )
    if status is BrowserRunStatus.CANCELLED:
        return (
            "browser_run cancelled by caller",
            "Re-issue when the beat is not cancelled",
            "do not retry while cancel is requested",
        )
    if status is BrowserRunStatus.REFUSED:
        return (
            "browser_run refused before spawn",
            "Fix config (binary / DREAM_CHROMIUM_CDP_URL) or remove cloud helpers from code",
            "do not retry until the refusal reason is resolved",
        )
    if status is BrowserRunStatus.SCRIPT_ERROR:
        return (
            "browser-harness script exited non-zero",
            "Inspect stderr traceback; fix helpers / selectors; retry with a smaller step",
            "do not retry the identical failing script",
        )
    return (
        "browser_run completed",
        "Continue with the next research or craft step",
        "stop when the question is answered with cited URLs",
    )


__all__ = ["next_actions_for", "summary_for"]
