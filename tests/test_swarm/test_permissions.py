"""Spec 10 slice C — permission round-trip on the mailbox.

Pinned semantics:

- Worker writes ``pending/{request_id}.json``.
- Leader reads pending, decides, calls ``resolve`` which atomically writes
  ``resolved/{request_id}.json`` and deletes ``pending/{request_id}.json``.
- Worker polls ``read_resolved`` (or awaits ``wait_for_response``) and only
  proceeds on an explicit ``allowed=True``.
- Both files exist under the WORKTREE's ``.harness/swarm/{leader}/permissions/``
  tree so they are inspectable and can be committed alongside the worker's
  next change (spec 10 acceptance scenario "Permission round-trip is
  file-mediated and inspectable").
- ``allow_once`` is an ephemeral flag on the response — never persisted into
  any longer-lived policy file by this layer.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dream.swarm._paths import leader_permissions_dir
from dream.swarm._permissions import (
    PermissionMailbox,
    PermissionRequest,
    PermissionResponse,
)

# --- shape ----------------------------------------------------------------


def test_permission_request_round_trips() -> None:
    req = PermissionRequest(
        request_id="perm-1",
        worker_id="generator",
        tool_name="file_write",
        tool_input={"path": "x", "content": "y"},
        description="write outside allowed paths",
    )
    assert PermissionRequest.from_dict(req.to_dict()) == req


def test_permission_response_round_trips() -> None:
    resp = PermissionResponse(
        request_id="perm-1",
        allowed=True,
        reason="ok",
        allow_once=True,
    )
    assert PermissionResponse.from_dict(resp.to_dict()) == resp


def test_permission_response_allow_once_defaults_false() -> None:
    resp = PermissionResponse(request_id="perm-1", allowed=False, reason="no")
    assert resp.allow_once is False


# --- write_pending / list_pending / read_pending --------------------------


def test_write_pending_lands_in_pending_subdir(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    pending = tmp_path / ".harness" / "swarm" / "planner" / "permissions" / "pending"
    assert (pending / "perm-1.json").is_file()


def test_list_pending_returns_oldest_first(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    older = PermissionRequest(
        request_id="perm-1",
        worker_id="generator",
        tool_name="file_write",
        tool_input={"path": "x"},
        description="d",
    )
    older.created_at = 1.0
    newer = PermissionRequest(
        request_id="perm-2",
        worker_id="generator",
        tool_name="file_write",
        tool_input={"path": "y"},
        description="d",
    )
    newer.created_at = 2.0
    pm.write_pending(newer)  # insert in reverse
    pm.write_pending(older)

    out = pm.list_pending()
    assert [r.request_id for r in out] == ["perm-1", "perm-2"]


def test_read_pending_by_id(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    got = pm.read_pending("perm-1")
    assert got is not None
    assert got.tool_name == "file_write"


def test_read_pending_returns_none_when_missing(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    assert pm.read_pending("never-existed") is None


# --- resolve --------------------------------------------------------------


def test_resolve_moves_file_from_pending_to_resolved(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    pm.resolve(request_id="perm-1", allowed=True, reason="ok")

    perms = tmp_path / ".harness" / "swarm" / "planner" / "permissions"
    assert not (perms / "pending" / "perm-1.json").exists()
    assert (perms / "resolved" / "perm-1.json").is_file()


def test_resolve_preserves_request_context_in_resolved(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x.txt", "content": "secret"},
            description="d",
        )
    )
    pm.resolve(request_id="perm-1", allowed=False, reason="path outside allowed roots")
    resp = pm.read_resolved("perm-1")
    assert resp is not None
    assert resp.request_id == "perm-1"
    assert resp.allowed is False
    assert resp.reason == "path outside allowed roots"


def test_resolve_raises_when_no_matching_pending(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    with pytest.raises(KeyError):
        pm.resolve(request_id="ghost", allowed=True, reason="x")


def test_resolve_is_idempotent_when_already_resolved(tmp_path: Path) -> None:
    # Re-resolving a request that has already been moved to resolved/ is a
    # no-op (returns False) rather than raising — leader retries shouldn't
    # crash the loop.
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    pm.resolve(request_id="perm-1", allowed=True, reason="ok")
    # Second call: not in pending → KeyError (caller distinguishes from the
    # already-resolved state via ``read_resolved``).
    with pytest.raises(KeyError):
        pm.resolve(request_id="perm-1", allowed=True, reason="ok")
    # First resolution preserved.
    resp = pm.read_resolved("perm-1")
    assert resp is not None
    assert resp.allowed is True


def test_resolve_allow_once_round_trips(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    pm.resolve(request_id="perm-1", allowed=True, reason="this once", allow_once=True)
    resp = pm.read_resolved("perm-1")
    assert resp is not None
    assert resp.allow_once is True


# --- read_resolved -------------------------------------------------------


def test_read_resolved_returns_none_before_resolution(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    assert pm.read_resolved("perm-1") is None


# --- wait_for_response ---------------------------------------------------


async def test_wait_for_response_returns_when_resolved(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )

    async def _resolver_later() -> None:
        await asyncio.sleep(0.05)
        pm.resolve(request_id="perm-1", allowed=True, reason="ok")

    waiter = asyncio.create_task(
        pm.wait_for_response("perm-1", timeout=2.0, poll_interval=0.01)
    )
    resolver_task = asyncio.create_task(_resolver_later())
    resp = await waiter
    assert resp.allowed is True
    await resolver_task


async def test_wait_for_response_times_out_cleanly(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    with pytest.raises(TimeoutError):
        await pm.wait_for_response("perm-1", timeout=0.05, poll_interval=0.01)


async def test_wait_for_response_only_returns_matching_id(tmp_path: Path) -> None:
    # Two outstanding requests; only the one whose id is being awaited
    # should unblock the waiter.
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    for rid in ("perm-1", "perm-2"):
        pm.write_pending(
            PermissionRequest(
                request_id=rid,
                worker_id="generator",
                tool_name="file_write",
                tool_input={"path": rid},
                description="d",
            )
        )

    async def _resolve_other() -> None:
        await asyncio.sleep(0.05)
        pm.resolve(request_id="perm-2", allowed=False, reason="no")

    other_task = asyncio.create_task(_resolve_other())
    # Waiter for perm-1 must NOT return when perm-2 is resolved.
    with pytest.raises(TimeoutError):
        await pm.wait_for_response("perm-1", timeout=0.2, poll_interval=0.01)
    await other_task


# --- on-disk shape (inspectable by operator) ----------------------------


def test_pending_file_is_human_readable_json(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    path = tmp_path / ".harness" / "swarm" / "planner" / "permissions" / "pending" / "perm-1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("request_id", "worker_id", "tool_name", "tool_input", "description", "created_at"):
        assert key in data


def test_resolved_file_records_decision_metadata(tmp_path: Path) -> None:
    pm = PermissionMailbox(leader_permissions_dir(tmp_path, "planner"))
    pm.write_pending(
        PermissionRequest(
            request_id="perm-1",
            worker_id="generator",
            tool_name="file_write",
            tool_input={"path": "x"},
            description="d",
        )
    )
    pm.resolve(request_id="perm-1", allowed=True, reason="ok")
    path = tmp_path / ".harness" / "swarm" / "planner" / "permissions" / "resolved" / "perm-1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("request_id", "allowed", "reason", "resolved_at"):
        assert key in data
