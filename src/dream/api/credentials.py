"""Credential pool + two-layer resilience state (Spec 02 §7-11).

The *pool* is the operator-facing surface: one TOML file, one pool per
substrate, every key has an optional human-readable label so the operator
can correlate a benched-credential warning with the line in
``credentials.toml`` that put it there.

The pool also owns the **outer** half of the two-layer resilience model: a
three-rung cooldown ladder per credential (30 s / 5 min / 60 min). The
**inner** half — per-call SDK retry against transient errors — lives in the
adapter's call site (today an OpenAI SDK that retries on its own) and is
classified, before reaching :meth:`CredentialPool.record_attempt`, as one of
the documented :data:`Outcome` values. Conflating the two layers is the bug
the spec calls out by name; keeping them in different modules makes the
conflation harder to write.

The loader (:func:`load_credential_pools`) is the startup gate: it refuses
to start with a loose credentials file or an empty active pool. Both
refusals are spec criteria 7, both surface before any model call.
"""

from __future__ import annotations

import os
import sys
import time
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Outcome = Literal[
    "success",
    "transient_retried_success",
    "transient_exhausted",
    "auth",
    "hard_refusal",
]
"""Classified call outcomes, fed in by the dispatch layer.

The classification table is Spec 02 §11. ``transient_retried_success``
and ``success`` are equivalent at the pool layer — the spec keeps the
distinction so observability can show that the SDK's inner retry actually
fired, even though the credential's rung is unaffected.
"""


_RUNG_COOLDOWNS_SECONDS: dict[int, float] = {
    1: 30.0,
    2: 5.0 * 60.0,
    3: 60.0 * 60.0,
}
"""Default rung → cooldown duration. Spec §10.

These are the defaults; an open question in the spec is whether they should
be configurable per-substrate. Until that's decided they're shared.
"""


class NoLiveCredential(RuntimeError):
    """Raised when :meth:`CredentialPool.pick_live` finds the pool entirely benched."""


class LooseCredentialsFile(PermissionError):
    """Raised when ``credentials.toml`` is readable by group or world (POSIX).

    Spec §7 (criterion 7) — better to refuse startup than to leak keys.
    """


class EmptyActivePool(RuntimeError):
    """Raised when the active substrate has zero credentials.

    Spec §7 — failing over on the first call would lose operator intent;
    refuse early instead.
    """


@dataclass
class Credential:
    """One key in a substrate's pool.

    ``rung`` is the cooldown-ladder position (0 = live, 1-3 = benched).
    ``cooldown_until`` is :func:`time.monotonic`-relative so the comparison
    in :meth:`is_benched` is wall-clock-jump safe.
    """

    label: str
    # ``repr=False``: the key is a secret. A bare repr (in a traceback, a debug
    # log, or ``logging.exception``) must never print it. ``label`` is the
    # operator-facing handle used everywhere a credential is referenced.
    key: str = field(repr=False)
    substrate: str
    rung: int = 0
    cooldown_until: float | None = None
    last_success: float | None = None
    last_error: str | None = None

    def is_benched(self) -> bool:
        if self.rung == 0:
            return False
        if self.cooldown_until is None:
            return False
        return time.monotonic() < self.cooldown_until


class CredentialPool:
    """Round-robin pool over the live subset of a substrate's credentials.

    The cursor advances on every :meth:`pick_live` call, even when the
    picked credential ends up returning an error — so a substrate with a
    handful of partially-failing keys distributes load across them instead
    of hammering the first live one.
    """

    def __init__(self, substrate: str, credentials: Iterable[Credential]) -> None:
        self.substrate = substrate
        self._credentials: list[Credential] = list(credentials)
        self._cursor = 0

    @classmethod
    def from_keys(cls, substrate: str, keys: Iterable[str]) -> CredentialPool:
        """Build a pool where each string is its own label and key.

        Convenience for tests and code that already holds raw key strings.
        Production loads go through :func:`load_credential_pools`.
        """
        creds = [Credential(label=k, key=k, substrate=substrate) for k in keys]
        return cls(substrate, creds)

    def is_empty(self) -> bool:
        return not self._credentials

    def get(self, label: str) -> Credential:
        for cred in self._credentials:
            if cred.label == label:
                return cred
        raise KeyError(f"no credential with label {label!r} in pool {self.substrate!r}")

    def live(self) -> list[Credential]:
        return [c for c in self._credentials if not c.is_benched()]

    def pick_live(self) -> Credential:
        """Round-robin over the live pool. Skips benched credentials (§8)."""
        n = len(self._credentials)
        if n == 0:
            raise NoLiveCredential(f"pool {self.substrate!r} is empty")
        for _ in range(n):
            cred = self._credentials[self._cursor % n]
            self._cursor = (self._cursor + 1) % n
            if not cred.is_benched():
                return cred
        raise NoLiveCredential(f"all credentials in pool {self.substrate!r} are benched")

    def record_attempt(self, label: str, *, outcome: Outcome) -> None:
        """Apply the §11 classification table to one credential.

        - ``success`` / ``transient_retried_success`` → reset rung to 0.
        - ``transient_exhausted`` → escalate one rung (1 → 2 → 3, capped).
        - ``auth`` → bench at rung 3 directly (§11, decision 11).
        - ``hard_refusal`` → no rung change (the credential is fine; the
          *request* was malformed).
        """
        cred = self.get(label)
        now = time.monotonic()

        if outcome in ("success", "transient_retried_success"):
            cred.rung = 0
            cred.cooldown_until = None
            cred.last_success = now
            return

        if outcome == "transient_exhausted":
            # Escalate one rung, capped at 3. From rung 0 this yields 1, so the
            # old ``... if rung > 0 else 1`` ternary was a no-op.
            cred.rung = min(cred.rung + 1, 3)
            cred.cooldown_until = now + _RUNG_COOLDOWNS_SECONDS[cred.rung]
            cred.last_error = outcome
            return

        if outcome == "auth":
            cred.rung = 3
            cred.cooldown_until = now + _RUNG_COOLDOWNS_SECONDS[3]
            cred.last_error = outcome
            return

        if outcome == "hard_refusal":
            cred.last_error = outcome
            return

        raise ValueError(f"unknown outcome {outcome!r}")


# --- Startup loader -------------------------------------------------------


def load_credential_pools(
    path: str | Path,
    active: str | None = None,
) -> dict[str, CredentialPool]:
    """Read ``credentials.toml`` and return ``{substrate → CredentialPool}``.

    Enforces Spec 02 §7 startup discipline:

    1. POSIX permission check — refuses to start if the file is group- or
       world-readable. The Windows equivalent (owner-only ACL check) is
       deferred; see the platform note below.
    2. If ``active`` is supplied, refuses to start when that substrate's
       pool is empty (or missing entirely).

    The TOML schema is per-substrate arrays of tables::

        [[openai]]
        key = "sk-..."
        label = "primary"

        [[openai]]
        key = "sk-..."
        label = "fallback"

    **Windows ACL note.** ``os.stat().st_mode`` on Windows always reports
    permissive POSIX-style bits regardless of the real ACL, so the bit
    check would false-positive on every Windows credentials file. Until
    a real ACL audit lands the loader trusts NTFS on Windows and only
    enforces bit-level perms on POSIX. The contract test for loose-file
    refusal skips on Windows accordingly.
    """
    p = Path(path)
    _enforce_permissions(p)

    text = p.read_text(encoding="utf-8")
    data = tomllib.loads(text) if text.strip() else {}

    pools: dict[str, CredentialPool] = {}
    for substrate, entries in data.items():
        if not isinstance(entries, list):
            continue
        creds: list[Credential] = []
        seen_labels: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key", "")).strip()
            if not key:
                continue
            label = str(entry.get("label", key))
            if label in seen_labels:
                # Operations address credentials by label; duplicates would make
                # benching/cooldown target the wrong key. Fail loud at load time.
                raise ValueError(
                    f"duplicate credential label {label!r} for substrate {substrate!r} in {p}"
                )
            seen_labels.add(label)
            creds.append(Credential(label=label, key=key, substrate=substrate))
        pools[substrate] = CredentialPool(substrate, creds)

    if active is not None:
        pool = pools.get(active)
        if pool is None or pool.is_empty():
            raise EmptyActivePool(
                f"no credentials configured for active substrate {active!r} in {p}"
            )

    return pools


def _enforce_permissions(path: Path) -> None:
    if sys.platform == "win32":
        # No portable owner-only ACL check yet (st_mode is meaningless on NTFS).
        # Fail *closed* rather than silently trusting a possibly world-readable
        # file: an operator who has secured the ACL out-of-band can acknowledge
        # the gap explicitly via the env override.
        if os.environ.get("DREAM_ALLOW_INSECURE_WINDOWS_CREDS") == "1":
            return
        raise LooseCredentialsFile(
            f"cannot verify owner-only permissions for {path} on Windows. Secure the "
            "file's ACL, then set DREAM_ALLOW_INSECURE_WINDOWS_CREDS=1 to proceed."
        )
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise LooseCredentialsFile(
            f"credentials file {path} has loose permissions (mode={oct(mode)}); "
            f"must be owner-only (0600). Run: chmod 600 {path}"
        )


# Re-exports kept terse; the dispatcher and adapter sites import these.
__all__ = [
    "Credential",
    "CredentialPool",
    "EmptyActivePool",
    "LooseCredentialsFile",
    "NoLiveCredential",
    "Outcome",
    "load_credential_pools",
]
