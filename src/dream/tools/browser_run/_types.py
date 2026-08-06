"""Typed contracts for ``browser_run`` (no stringly status / bare dict access)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

BROWSER_RUN_BIN_KEY: Final[str] = "dream.browser_harness.bin"
BROWSER_RUN_CDP_URL_KEY: Final[str] = "dream.browser.cdp_url"
BROWSER_RUN_CDP_WS_KEY: Final[str] = "dream.browser.cdp_ws"
BROWSER_RUN_DISABLED_KEY: Final[str] = "dream.browser_harness.disabled"

CDP_URL_ENV: Final[str] = "DREAM_CHROMIUM_CDP_URL"
CDP_WS_ENV: Final[str] = "DREAM_CHROMIUM_CDP_WS"
BIN_ENV: Final[str] = "DREAM_BROWSER_HARNESS_BIN"

DEFAULT_TIMEOUT_SECONDS: Final[float] = 120.0
MAX_TIMEOUT_SECONDS: Final[float] = 600.0
OUTPUT_CAP: Final[int] = 12_000

# The self-hosted-only policy lives once, here (DRY): the code markers refused before spawn and the
# env keys stripped at spawn. Browser Use Cloud is never reached for.
CLOUD_ADMIN_MARKERS: Final[tuple[str, ...]] = ("start_remote_daemon", "stop_remote_daemon")
CLOUD_ENV_KEYS: Final[tuple[str, ...]] = ("BROWSER_USE_API_KEY", "BU_AUTOSPAWN", "BU_BROWSER_ID")

# The session/operator metadata block threaded through tool callbacks.
Metadata: TypeAlias = dict[str, Any]


class BrowserRunStatus(StrEnum):
    """Terminal status for one browser_run invocation."""

    SUCCESS = "success"
    SCRIPT_ERROR = "script_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    REFUSED = "refused"
    SETUP_REQUIRED = "setup_required"


class BrowserKind(StrEnum):
    """How the daemon reached Chromium."""

    CDP = "cdp"
    UNKNOWN = "unknown"


class BrowserRunOutcome(BaseModel):
    """Structured parent-facing outcome (mirrored into ToolResult.structured)."""

    model_config = ConfigDict(extra="forbid")

    status: BrowserRunStatus
    exit_code: int | None = None
    page: dict[str, Any] | None = None
    dialog: dict[str, Any] | None = None
    browser_kind: BrowserKind = BrowserKind.CDP
    bu_name: str = "default"
    duration_seconds: float = 0.0
    url: str | None = None


class BrowserRunInput(BaseModel):
    """Arguments for the ``browser_run`` tool."""

    code: str = Field(
        description=(
            "Python executed by browser-harness with helpers pre-imported "
            "(page_info, new_tab, click_at_xy, cdp, js, wait_for_load, …). "
            "First navigation: new_tab(url). End with "
            "print(json.dumps({...})) for structured output."
        ),
    )
    name: str = Field(
        default="default",
        description="BU_NAME — daemon namespace. Use distinct names for parallel beats.",
        pattern=r"^[a-zA-Z0-9_-]{1,32}$",
    )
    timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        gt=0,
        le=MAX_TIMEOUT_SECONDS,
        description="Per-call wall-clock timeout in seconds.",
    )


def code_requests_cloud(code: str) -> bool:
    """True when ``code`` tries to start/stop Browser Use cloud daemons."""
    return any(marker in code for marker in CLOUD_ADMIN_MARKERS)


__all__ = [
    "BIN_ENV",
    "BROWSER_RUN_BIN_KEY",
    "BROWSER_RUN_CDP_URL_KEY",
    "BROWSER_RUN_CDP_WS_KEY",
    "BROWSER_RUN_DISABLED_KEY",
    "CDP_URL_ENV",
    "CDP_WS_ENV",
    "CLOUD_ADMIN_MARKERS",
    "CLOUD_ENV_KEYS",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "OUTPUT_CAP",
    "BrowserKind",
    "BrowserRunInput",
    "BrowserRunOutcome",
    "BrowserRunStatus",
    "Metadata",
    "code_requests_cloud",
]
