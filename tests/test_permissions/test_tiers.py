"""Spec 13A — tier model: the default session posture."""

from __future__ import annotations

from dream.permissions._tiers import DEFAULT_TIER
from dream.permissions._types import SandboxTier


def test_default_tier_is_repo_write() -> None:
    assert DEFAULT_TIER is SandboxTier.REPO_WRITE
