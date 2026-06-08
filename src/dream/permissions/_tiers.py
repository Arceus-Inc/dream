"""Tier model: the default session posture.

The four-tier ordering lives on :class:`~dream.permissions._types.SandboxTier`
itself, and the tier each gated effect requires lives on
:class:`~dream.permissions._types.Effect`. This module only names the default
posture a session runs at.
"""

from __future__ import annotations

from dream.permissions._types import SandboxTier

#: The posture a session runs at unless an operator opts higher. Never
#: ``UNRESTRICTED`` and never network-enabled by default.
DEFAULT_TIER = SandboxTier.REPO_WRITE
