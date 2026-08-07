"""Spec 05 slice A — ``ToolExecutionContext`` structural contract.

The concrete context lives in ``dream.tools._context`` and MUST structurally
satisfy the public ``dream.contracts.tool.ToolContext`` Protocol so engine code
that types against the Protocol can accept it without coupling to the impl.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from dream.contracts.credentials import CredentialBrokerPort
from dream.contracts.tool import ToolContext
from dream.tools._context import ToolExecutionContext


def test_context_carries_working_dir_and_session_id(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_abc")
    assert ctx.working_dir == tmp_path
    assert ctx.session_id == "s_abc"


def test_context_cancel_requested_defaults_false(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_abc")
    assert ctx.cancel_requested is False


def test_context_cancel_can_be_flipped(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_abc")
    ctx.request_cancel()
    assert ctx.cancel_requested is True


def test_context_carries_typed_credential_broker(tmp_path: Path) -> None:
    broker = cast(CredentialBrokerPort, object())
    ctx = ToolExecutionContext(
        working_dir=tmp_path,
        session_id="session",
        credential_broker=broker,
    )
    assert ctx.credential_broker is broker


def test_context_metadata_defaults_to_empty_dict(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_abc")
    assert ctx.metadata == {}


def test_context_satisfies_protocol_structurally(tmp_path: Path) -> None:
    """Structural Protocol check — runtime_checkable Protocol membership."""
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_abc")
    # Both attributes Protocol requires:
    assert hasattr(ctx, "working_dir")
    assert hasattr(ctx, "session_id")
    assert hasattr(ctx, "cancel_requested")
    assert hasattr(ctx, "run_subprocess")
    assert hasattr(ctx, "spill_large_output")
    # And static type-checks against the Protocol (compile-time guarantee).
    _accepted: ToolContext = ctx
    assert _accepted is ctx
