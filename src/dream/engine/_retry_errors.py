"""Typed exceptions the retries stack raises across the streamer ↔ act-loop seam."""

from __future__ import annotations

from dream.api.error_classify import FailureKind


class CompressRequired(Exception):
    """Provider rejected the payload as too large — act-loop should shrink + retry once.

    Raised by :class:`~dream.engine.FailoverStreamer` instead of raw HTTP errors so
    the loop does not have to re-classify overflow. Carries the original cause for
    diagnostics; never embeds secret material.
    """

    def __init__(self, *, cause: BaseException, kind: FailureKind) -> None:
        super().__init__(f"compress required ({kind.value})")
        self.cause = cause
        self.kind = kind


__all__ = ["CompressRequired"]
