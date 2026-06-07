"""UI verification seam (Spec 12c).

"Verify like a user" wants a browser driving the running app for UI changes. That
needs a live Playwright MCP server, so the real driver is a deferred leftover
spec. Here we define the seam (``UiVerifier``) and the default (``SkipUiVerifier``)
which records each user-path as ``skipped`` — exactly the spec's
Playwright-unavailable behaviour (never silently passed).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from dream.verification._types import RepoVerificationStep


@runtime_checkable
class UiVerifier(Protocol):
    """Verify one user-facing path; returns a verification step result."""

    async def verify(self, user_path: str) -> RepoVerificationStep: ...


class SkipUiVerifier:
    """Default UI verifier: records the path as ``skipped`` (no browser)."""

    async def verify(self, user_path: str) -> RepoVerificationStep:
        return RepoVerificationStep(
            command=f"ui:{user_path}",
            status="skipped",
            name=user_path,
            stderr="UI verification skipped: no Playwright MCP verifier configured",
        )


__all__ = ["SkipUiVerifier", "UiVerifier"]
