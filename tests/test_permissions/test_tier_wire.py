"""Spec 13B — SandboxTier wire-string mapping (config <-> enum)."""

from __future__ import annotations

import pytest

from dream.permissions._types import SandboxTier


def test_wire_round_trips_every_tier() -> None:
    for tier in SandboxTier:
        assert SandboxTier.from_wire(tier.wire) is tier


def test_known_wire_strings() -> None:
    assert SandboxTier.from_wire("read-only") is SandboxTier.READ_ONLY
    assert SandboxTier.from_wire("repo-write") is SandboxTier.REPO_WRITE
    assert SandboxTier.from_wire("repo-write+net-allowlist") is SandboxTier.REPO_WRITE_NET
    assert SandboxTier.from_wire("unrestricted") is SandboxTier.UNRESTRICTED


def test_unknown_wire_string_raises() -> None:
    with pytest.raises(ValueError):
        SandboxTier.from_wire("god-mode")


def test_ordering_preserved_after_adding_wire() -> None:
    assert (
        SandboxTier.READ_ONLY
        < SandboxTier.REPO_WRITE
        < SandboxTier.REPO_WRITE_NET
        < SandboxTier.UNRESTRICTED
    )
