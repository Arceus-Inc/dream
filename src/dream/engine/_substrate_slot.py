"""One substrate in the failover chain: credential pool + labeled streamers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dream.api.credentials import CredentialPool
from dream.engine._loop import TurnStreamer


@dataclass(frozen=True)
class SubstrateSlot:
    """A named substrate with its outer-loop pool and per-credential streamers.

    Every credential label in ``pool`` must have a matching entry in
    ``streamers`` — the streamer is built with that credential's key at
    harness construction time so the failover path never mutates keys via
    ``setattr``.
    """

    name: str
    pool: CredentialPool
    streamers: Mapping[str, TurnStreamer]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("SubstrateSlot.name must be non-empty")
        if self.pool.substrate != self.name:
            raise ValueError(
                f"pool substrate {self.pool.substrate!r} does not match slot name {self.name!r}"
            )
        labels = {cred.label for cred in self.pool.all_credentials()}
        missing = labels - frozenset(self.streamers)
        if missing:
            raise ValueError(
                f"SubstrateSlot {self.name!r} missing streamers for labels: {sorted(missing)}"
            )
        extra = frozenset(self.streamers) - labels
        if extra:
            raise ValueError(
                f"SubstrateSlot {self.name!r} has streamers without credentials: {sorted(extra)}"
            )


__all__ = ["SubstrateSlot"]
