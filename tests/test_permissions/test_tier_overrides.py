"""Spec 13B — tool-tier-overrides.toml loader + 365-day staleness (via Clock)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from dream.permissions._overrides import (
    TierOverrideError,
    parse_tier_overrides,
    read_tier_overrides,
)
from dream.permissions._types import SandboxTier
from dream.utils.clock import FakeClock

DAY_MS = 86_400_000
PROMOTED = "2025-01-01T00:00:00Z"


def _epoch_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def test_missing_file_yields_empty(tmp_path: Path) -> None:
    ov = read_tier_overrides(tmp_path / "absent.toml", clock=FakeClock())
    assert dict(ov.required_tier) == {}
    assert ov.warnings == ()


def test_parse_promotes_tool() -> None:
    ov = parse_tier_overrides(
        '[playwright]\ntier_required = "repo-write+net-allowlist"\n',
        clock=FakeClock(),
    )
    assert ov.required_tier["playwright"] is SandboxTier.REPO_WRITE_NET


def test_multiple_tools() -> None:
    ov = parse_tier_overrides(
        '[a]\ntier_required = "repo-write"\n[b]\ntier_required = "read-only"\n',
        clock=FakeClock(),
    )
    assert ov.required_tier == {
        "a": SandboxTier.REPO_WRITE,
        "b": SandboxTier.READ_ONLY,
    }


def test_missing_tier_required_raises() -> None:
    with pytest.raises(TierOverrideError):
        parse_tier_overrides('[playwright]\nreason = "x"\n', clock=FakeClock())


def test_unknown_tier_string_raises() -> None:
    with pytest.raises(TierOverrideError):
        parse_tier_overrides('[x]\ntier_required = "god-mode"\n', clock=FakeClock())


def test_fresh_promotion_has_no_warning() -> None:
    now = _epoch_ms(PROMOTED) + 100 * DAY_MS
    ov = parse_tier_overrides(
        f'[x]\ntier_required = "repo-write"\npromoted_at = "{PROMOTED}"\n',
        clock=FakeClock(start_ms=now),
    )
    assert ov.warnings == ()


def test_stale_promotion_warns() -> None:
    now = _epoch_ms(PROMOTED) + 400 * DAY_MS
    ov = parse_tier_overrides(
        f'[x]\ntier_required = "repo-write"\npromoted_at = "{PROMOTED}"\n',
        clock=FakeClock(start_ms=now),
    )
    assert len(ov.warnings) == 1
    assert "x" in ov.warnings[0]


def test_promotion_without_timestamp_never_warns() -> None:
    ov = parse_tier_overrides(
        '[x]\ntier_required = "repo-write"\n',
        clock=FakeClock(start_ms=10**15),
    )
    assert ov.warnings == ()


def test_bad_timestamp_raises() -> None:
    with pytest.raises(TierOverrideError):
        parse_tier_overrides(
            '[x]\ntier_required = "repo-write"\npromoted_at = "not-a-date"\n',
            clock=FakeClock(),
        )
