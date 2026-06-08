"""Spec 10 slice A — ``request_capability`` event is recordable, never widening.

Decision #8 (capability minimisation): a role cannot expand its own toolset
mid-session. The only escalation path is a recordable ``request_capability``
event the parent runner can choose to act on by re-spawning with a different
manifest. This file pins that the event exists *and* that producing one has
no effect on the live minimum toolset.
"""

from __future__ import annotations

import time

from dream.permissions import SandboxTier
from dream.roles import (
    RequestCapabilityEvent,
    RoleManifest,
    compute_minimum_toolset,
    request_capability,
)
from dream.tools._base import ToolDeclaration


def _decl(tier: int = 1) -> ToolDeclaration:
    return ToolDeclaration(risk="mutating", tier_required=tier, timeout_seconds=10.0)


def test_request_capability_event_carries_role_tool_and_reason() -> None:
    ev = request_capability(role="planner", tool_name="bash", reason="needs to run ls")
    assert isinstance(ev, RequestCapabilityEvent)
    assert ev.role == "planner"
    assert ev.tool_name == "bash"
    assert ev.reason == "needs to run ls"
    # Timestamp is iso8601-ish (sortable).
    assert isinstance(ev.ts, str)
    assert len(ev.ts) >= len("2026-01-01T00:00:00")


def test_request_capability_is_recordable_dict_shape() -> None:
    ev = request_capability(role="generator", tool_name="external_call", reason="x")
    payload = ev.to_dict()
    assert payload["type"] == "role.request_capability"
    assert payload["role"] == "generator"
    assert payload["tool_name"] == "external_call"
    assert payload["reason"] == "x"
    assert "ts" in payload


def test_request_capability_does_not_widen_minimum_toolset() -> None:
    m = RoleManifest(
        name="planner",
        description="d",
        system_prompt="p",
        tools=["file_read"],
    )
    declarations = {"file_read": _decl(0), "bash": _decl(1)}

    before = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
    )
    # Producing the event has no side effects on the manifest or its toolset.
    _ = request_capability(role="planner", tool_name="bash", reason="please")
    after = compute_minimum_toolset(
        m, sandbox_tier=SandboxTier.REPO_WRITE, declarations=declarations
    )

    assert after == before
    assert "bash" not in after


def test_request_capability_timestamps_monotonic_per_call() -> None:
    a = request_capability(role="planner", tool_name="bash", reason="r")
    time.sleep(0.001)
    b = request_capability(role="planner", tool_name="bash", reason="r")
    assert a.ts <= b.ts
