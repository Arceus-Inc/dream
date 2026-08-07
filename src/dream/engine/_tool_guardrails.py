"""Hermes-style tool-call circuit breaker (exact-failure streaks).

Advisory ``safe_retry`` / ``stop_condition`` remain model-facing. This module
enforces a hard stop when the same tool + args + error repeats past a bound —
so a thrashing beat cannot burn the turn budget on one failing call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final


class GuardrailVerdict(Enum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


_DEFAULT_WARN_AFTER: Final[int] = 2
_DEFAULT_BLOCK_AFTER: Final[int] = 5


def fingerprint_args(arguments: Mapping[str, object]) -> str:
    """Stable hash of tool arguments (order-independent)."""
    payload = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ToolGuardrails:
    """Track exact-failure streaks within one session/dispatcher lifetime."""

    warn_after: int = _DEFAULT_WARN_AFTER
    block_after: int = _DEFAULT_BLOCK_AFTER
    _exact_counts: dict[str, int] = field(default_factory=dict)

    def observe_error(
        self, *, tool: str, args_fingerprint: str, error_key: str
    ) -> GuardrailVerdict:
        key = f"{tool}|{args_fingerprint}|{error_key}"
        count = self._exact_counts.get(key, 0) + 1
        self._exact_counts[key] = count
        if count >= self.block_after:
            return GuardrailVerdict.BLOCK
        if count >= self.warn_after:
            return GuardrailVerdict.WARN
        return GuardrailVerdict.ALLOW

    def observe_success(self, *, tool: str, args_fingerprint: str) -> None:
        """Clear exact-failure streaks for this tool+args on any success."""
        prefix = f"{tool}|{args_fingerprint}|"
        doomed = [k for k in self._exact_counts if k.startswith(prefix)]
        for key in doomed:
            del self._exact_counts[key]


__all__ = [
    "GuardrailVerdict",
    "ToolGuardrails",
    "fingerprint_args",
]
