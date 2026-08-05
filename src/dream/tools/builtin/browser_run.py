"""``browser_run`` — first-class browser control via browser-harness + Chromium CDP.

Self-hosted Chromium only (``DREAM_CHROMIUM_CDP_URL`` / ``BU_CDP_URL``). Browser Use
Cloud helpers are refused. Does not mutate the worktree — shadow checkpoints skip it.
"""

from __future__ import annotations

from typing import Any

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools.browser_run._observation import next_actions_for, summary_for
from dream.tools.browser_run._parse import looks_like_setup_error, parse_structured
from dream.tools.browser_run._spawn import build_spawn_config, run_browser_harness
from dream.tools.browser_run._types import (
    OUTPUT_CAP,
    BrowserKind,
    BrowserRunInput,
    BrowserRunOutcome,
    BrowserRunStatus,
    code_requests_cloud,
)
from dream.tools.execute_code._hygiene import sanitize_output

_DESCRIPTION = (
    "Drive a real Chromium browser via browser-harness (CDP). Helpers are pre-imported: "
    "page_info, new_tab, click_at_xy, cdp, js, wait_for_load, ensure_real_tab. "
    "First navigation: new_tab(url), then wait_for_load(). "
    "Use for search, reading JS-heavy pages, forms, and logged-in flows. "
    'End with print(json.dumps({"page": page_info(), ...})) for structured results. '
    "Login/MFA walls: stop and ask the operator. Cloud browsers are disabled — "
    "the org Chromium CDP endpoint is used."
)


class BrowserRunTool(BaseTool):
    """Run a browser-harness Python script against the configured Chromium CDP endpoint."""

    name = "browser_run"
    description = _DESCRIPTION
    declaration = ToolDeclaration(risk="external", tier_required=2, timeout_seconds=600.0)
    input_model = BrowserRunInput

    def is_read_only(self) -> bool:
        # Browser DOM ≠ worktree; shadow checkpoints must not fire.
        return True

    def is_read_only_for(self, input: dict[str, Any]) -> bool:
        return True

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        return ToolEffects(network_host="chromium-cdp")

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = BrowserRunInput.model_validate(input)
        if not args.code.strip():
            return _refused("No code provided.", bu_name=args.name)

        if code_requests_cloud(args.code):
            return _refused(
                "Cloud browsers are disabled. Use the configured Chromium CDP endpoint "
                "(DREAM_CHROMIUM_CDP_URL); do not call start_remote_daemon/stop_remote_daemon.",
                bu_name=args.name,
            )

        if ctx.cancel_requested:
            return _status_result(
                BrowserRunOutcome(
                    status=BrowserRunStatus.CANCELLED,
                    bu_name=args.name,
                    browser_kind=BrowserKind.CDP,
                ),
                content="browser_run cancelled before spawn",
            )

        config = build_spawn_config(metadata=ctx.metadata, bu_name=args.name)
        if isinstance(config, str):
            return _refused(config, bu_name=args.name)

        spawned = await run_browser_harness(
            config=config,
            code=args.code,
            timeout_seconds=args.timeout_seconds,
            cancel_requested=lambda: ctx.cancel_requested,
        )

        if spawned.cancelled:
            return _status_result(
                BrowserRunOutcome(
                    status=BrowserRunStatus.CANCELLED,
                    bu_name=args.name,
                    browser_kind=BrowserKind.CDP,
                    duration_seconds=spawned.duration_seconds,
                ),
                content=spawned.stderr or "browser_run cancelled",
            )

        if spawned.timed_out:
            return _status_result(
                BrowserRunOutcome(
                    status=BrowserRunStatus.TIMEOUT,
                    bu_name=args.name,
                    browser_kind=BrowserKind.CDP,
                    duration_seconds=spawned.duration_seconds,
                ),
                content=spawned.stderr or f"browser_run timed out after {args.timeout_seconds}s",
            )

        stdout = sanitize_output(spawned.stdout)
        stderr = sanitize_output(spawned.stderr)
        parsed = parse_structured(stdout)
        page = parsed.get("page") if isinstance(parsed.get("page"), dict) else None
        dialog = parsed.get("dialog") if isinstance(parsed.get("dialog"), dict) else None
        url = None
        if isinstance(page, dict) and isinstance(page.get("url"), str):
            url = page["url"]
        elif isinstance(parsed.get("url"), str):
            url = parsed["url"]

        content = _compose_content(stdout, stderr)
        if looks_like_setup_error(stderr, stdout) and spawned.returncode not in (0, None):
            status = BrowserRunStatus.SETUP_REQUIRED
        elif spawned.returncode == 0:
            status = BrowserRunStatus.SUCCESS
        else:
            status = BrowserRunStatus.SCRIPT_ERROR

        outcome = BrowserRunOutcome(
            status=status,
            exit_code=spawned.returncode,
            page=page,
            dialog=dialog,
            browser_kind=BrowserKind.CDP,
            bu_name=args.name,
            duration_seconds=spawned.duration_seconds,
            url=url,
        )
        return _status_result(
            outcome, content=content, is_error=status is not BrowserRunStatus.SUCCESS
        )


def _compose_content(stdout: str, stderr: str) -> str:
    body = f"{stdout}\n--- stderr ---\n{stderr}" if stderr and stdout else stderr or stdout
    if len(body) > OUTPUT_CAP:
        return body[: OUTPUT_CAP - 20] + "\n...[truncated]..."
    return body


def _refused(message: str, *, bu_name: str) -> ToolResult:
    return _status_result(
        BrowserRunOutcome(
            status=BrowserRunStatus.REFUSED,
            bu_name=bu_name,
            browser_kind=BrowserKind.CDP,
        ),
        content=message,
        is_error=True,
    )


def _status_result(
    outcome: BrowserRunOutcome,
    *,
    content: str,
    is_error: bool | None = None,
) -> ToolResult:
    err = outcome.status is not BrowserRunStatus.SUCCESS if is_error is None else is_error
    root, retry, stop = next_actions_for(outcome.status)
    metadata: dict[str, Any] = {
        "summary": summary_for(outcome),
        "returncode": outcome.exit_code,
        "browser_kind": outcome.browser_kind.value,
        "bu_name": outcome.bu_name,
        "duration_seconds": outcome.duration_seconds,
        "artifacts": [],
    }
    if outcome.url:
        metadata["url"] = outcome.url
    if err:
        metadata.update(
            {
                "root_cause": root,
                "safe_retry": retry,
                "stop_condition": stop,
            }
        )
    return ToolResult(
        content=content,
        structured=outcome.model_dump(mode="json"),
        is_error=err,
        metadata=metadata,
    )


__all__ = ["BrowserRunTool"]
