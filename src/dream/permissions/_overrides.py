"""Trust-ramp overrides — ``.harness/tool-tier-overrides.toml`` (Spec 13B).

Discovered tools/MCPs start at ``read-only`` regardless of self-declaration
(AC #24); an operator promotes one by adding a ``[tool]`` table here with a
``tier_required`` (and, by convention, ``promoted_by``/``promoted_at``/``reason``
for the audit trail). Promotions older than 365 days are surfaced as *warnings*
(data, never logged) so a session can flag stale trust. A missing file means no
promotions; a malformed file fails fast.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dream.permissions._types import SandboxTier
from dream.utils.clock import Clock

STALE_AFTER_MS = 365 * 86_400_000


class TierOverrideError(ValueError):
    """Raised when ``tool-tier-overrides.toml`` is malformed."""


@dataclass(frozen=True)
class TierOverrides:
    """Operator promotions plus any staleness warnings (surfaced as data)."""

    required_tier: dict[str, SandboxTier]
    warnings: tuple[str, ...]


def parse_tier_overrides(text: str, *, clock: Clock) -> TierOverrides:
    """Parse the override body into promotions + staleness warnings."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TierOverrideError(f"invalid tool-tier-overrides TOML: {exc}") from exc

    now_ms = clock.now_ms()
    required: dict[str, SandboxTier] = {}
    warnings: list[str] = []
    for name, table in data.items():
        if not isinstance(table, dict):
            raise TierOverrideError(f"'[{name}]' must be a table, got {type(table).__name__}")
        required[name] = _tier_required(name, table)
        promoted_at = table.get("promoted_at")
        if promoted_at is not None and _is_stale(name, promoted_at, now_ms):
            warnings.append(
                f"tool {name!r} tier promotion is stale "
                f"(promoted {promoted_at}, older than 365 days)"
            )
    return TierOverrides(required_tier=required, warnings=tuple(warnings))


def read_tier_overrides(path: Path, *, clock: Clock) -> TierOverrides:
    """Read + parse the overrides; a missing file yields no promotions."""
    if not path.is_file():
        return TierOverrides(required_tier={}, warnings=())
    return parse_tier_overrides(path.read_text(encoding="utf-8"), clock=clock)


def _tier_required(name: str, table: dict[str, Any]) -> SandboxTier:
    raw = table.get("tier_required")
    if not isinstance(raw, str):
        raise TierOverrideError(f"'[{name}]' is missing a string 'tier_required'")
    try:
        return SandboxTier.from_wire(raw)
    except ValueError as exc:
        raise TierOverrideError(f"'[{name}]': {exc}") from exc


def _is_stale(name: str, promoted_at: Any, now_ms: int) -> bool:
    moment = _to_datetime(name, promoted_at)
    return now_ms - int(moment.timestamp() * 1000) > STALE_AFTER_MS


def _to_datetime(name: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise TierOverrideError(
                f"'[{name}]' invalid promoted_at {value!r}: {exc}"
            ) from exc
    else:
        raise TierOverrideError(f"'[{name}]' promoted_at must be a datetime or ISO string")
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
