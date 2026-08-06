"""Parse browser-harness stdout into structured page/dialog facts."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_BH_JSON_PREFIX = "__BH_JSON__"
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
# Pre-lowercased: the probe blob is lowercased before matching.
_SETUP_MARKERS: tuple[str, ...] = (
    "remote debugging",
    "chrome-not-running",
    "devtoolsactiveport",
    "bu_cdp_url=",
    "permission-blocked",
    "browser-harness:",
)


def looks_like_setup_error(stderr: str, stdout: str = "") -> bool:
    """Heuristic: daemon could not attach to Chromium CDP."""
    blob = f"{stderr}\n{stdout}".lower()
    return any(marker in blob for marker in _SETUP_MARKERS) and (
        "unreachable" in blob
        or "not found" in blob
        or "not running" in blob
        or "permission-blocked" in blob
        or "turned off" in blob
        or "enable chrome://" in blob
    )


def parse_structured(stdout: str) -> dict[str, Any]:
    """Extract structured payload from stdout (trailing JSON / page_info).

    Priority:
    1. Last line starting with ``__BH_JSON__``
    2. Last non-empty line that parses as a JSON/Python dict
    3. URL guess from any https?:// match
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith(_BH_JSON_PREFIX):
            payload = _loads(line[len(_BH_JSON_PREFIX) :])
            if isinstance(payload, dict):
                return _normalize(payload)

    for line in reversed(lines):
        payload = _loads(line)
        if isinstance(payload, dict):
            return _normalize(payload)

    match = _URL_RE.search(stdout)
    if match:
        return {"url": match.group(0).rstrip(".,);]")}
    return {}


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, dict) else None


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a bare ``page_info()`` shape as ``{"page": ...}``; pass everything else through."""
    if "page" in payload or "dialog" in payload:
        return payload
    if "url" in payload and ("title" in payload or "w" in payload):
        return {"page": payload}
    return payload


__all__ = ["looks_like_setup_error", "parse_structured"]
