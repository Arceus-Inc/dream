"""Spec 05 slice A — ``ToolDeclaration`` shape + validation.

Every tool MUST declare ``risk`` and ``tier_required`` (spec acceptance #6);
missing either is a session-blocking validation error.
"""

from __future__ import annotations

import pytest

from dream.tools._base import ToolDeclaration, ToolDeclarationError


def test_declaration_is_frozen() -> None:
    decl = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=30.0)
    with pytest.raises((AttributeError, TypeError)):
        decl.risk = "mutating"  # type: ignore[misc]


def test_declaration_rejects_unknown_risk() -> None:
    with pytest.raises(ToolDeclarationError, match="risk"):
        ToolDeclaration(risk="nuclear", tier_required=0, timeout_seconds=30.0)  # type: ignore[arg-type]


def test_declaration_rejects_negative_tier() -> None:
    with pytest.raises(ToolDeclarationError, match="tier_required"):
        ToolDeclaration(risk="safe", tier_required=-1, timeout_seconds=30.0)


def test_declaration_rejects_non_positive_timeout() -> None:
    with pytest.raises(ToolDeclarationError, match="timeout_seconds"):
        ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=0.0)
    with pytest.raises(ToolDeclarationError, match="timeout_seconds"):
        ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=-1.5)


@pytest.mark.parametrize("risk", ["safe", "mutating", "external"])
def test_declaration_accepts_each_risk_class(risk: str) -> None:
    decl = ToolDeclaration(risk=risk, tier_required=1, timeout_seconds=60.0)  # type: ignore[arg-type]
    assert decl.risk == risk
    assert decl.tier_required == 1
    assert decl.timeout_seconds == 60.0
