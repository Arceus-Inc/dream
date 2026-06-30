"""Worker ↔ leader permission round-trip on the swarm filesystem.

Layout (under ``<worktree>/.harness/swarm/{leader}/permissions/``):

    pending/<request_id>.json    — worker → leader, awaiting decision
    resolved/<request_id>.json   — leader's reply, deletes the pending file

Flow (spec 10 acceptance scenario "Permission round-trip is file-mediated
and inspectable"):

    1. Worker constructs a ``PermissionRequest`` and calls
       :meth:`PermissionMailbox.write_pending` — file lands in ``pending/``.
    2. Leader's loop calls :meth:`list_pending` between turns, decides
       (e.g. via ``dream.permissions.PermissionChecker``), then calls
       :meth:`resolve` which atomically replaces ``pending/<id>.json`` with
       ``resolved/<id>.json``.
    3. Worker awaits :meth:`wait_for_response` (or polls :meth:`read_resolved`)
       and only proceeds when the response says so.

Both files persist on disk so the operator can inspect (and the worker can
commit) a permission decision alongside the change it authorised.

The ``allow_once`` flag on the response is **ephemeral** — this layer just
shuttles it back to the worker. Promoting an ``allow_once`` decision into a
longer-lived ``Policy.tool_allow`` rule is the caller's job, intentionally
(spec 10: "allow-once never persists into a rule file").
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dream.utils.fs import is_json_drop_file, save_json_file, try_load_json_file

__all__ = ["PermissionMailbox", "PermissionRequest", "PermissionResponse"]


# --- data shapes --------------------------------------------------------


@dataclass
class PermissionRequest:
    """Worker → leader request for a privileged tool call."""

    request_id: str
    worker_id: str
    tool_name: str
    # The tool-call argument map for ``tool_name`` (e.g. ``{"path": ...,
    # "content": ...}`` for a write tool) — arbitrary per-tool JSON.
    tool_input: dict[str, Any]
    description: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionRequest:
        return cls(
            request_id=data["request_id"],
            worker_id=data["worker_id"],
            tool_name=data["tool_name"],
            tool_input=dict(data.get("tool_input") or {}),
            description=data.get("description", ""),
            created_at=float(data.get("created_at", time.time())),
        )


@dataclass
class PermissionResponse:
    """Leader → worker decision on a permission request."""

    request_id: str
    allowed: bool
    reason: str = ""
    allow_once: bool = False
    resolved_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionResponse:
        return cls(
            request_id=data["request_id"],
            allowed=bool(data["allowed"]),
            reason=data.get("reason", ""),
            allow_once=bool(data.get("allow_once", False)),
            resolved_at=float(data.get("resolved_at", time.time())),
        )


# --- the mailbox --------------------------------------------------------


@dataclass
class PermissionMailbox:
    """Filesystem-backed permission queue rooted at one leader's directory.

    ``root`` is the leader's ``permissions/`` directory — typically
    ``<worktree>/.harness/swarm/{leader}/permissions``, as returned by
    :func:`dream.swarm._paths.leader_permissions_dir`.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # path helpers --------------------------------------------------

    @property
    def pending_dir(self) -> Path:
        return self.root / "pending"

    @property
    def resolved_dir(self) -> Path:
        return self.root / "resolved"

    def _pending_path(self, request_id: str) -> Path:
        return self.pending_dir / f"{request_id}.json"

    def _resolved_path(self, request_id: str) -> Path:
        return self.resolved_dir / f"{request_id}.json"

    # worker side: write + wait ------------------------------------

    def write_pending(self, request: PermissionRequest) -> Path:
        """Atomically write a pending request; return its on-disk path."""
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        dest = self._pending_path(request.request_id)
        save_json_file(dest, request.to_dict(), trailing_newline=False)
        return dest

    def read_resolved(self, request_id: str) -> PermissionResponse | None:
        """Return the resolution if one exists, else ``None``."""
        path = self._resolved_path(request_id)
        if not path.is_file():
            return None
        return try_load_json_file(path, PermissionResponse.from_dict)

    async def wait_for_response(
        self,
        request_id: str,
        *,
        timeout: float,
        poll_interval: float = 0.05,
    ) -> PermissionResponse:
        """Poll ``resolved/{request_id}.json`` until present or ``timeout``.

        Raises:
            TimeoutError: if no resolution appears within ``timeout`` seconds.
        """
        deadline = time.monotonic() + timeout
        while True:
            resp = self.read_resolved(request_id)
            if resp is not None:
                return resp
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"permission {request_id!r} not resolved within {timeout}s"
                )
            await asyncio.sleep(poll_interval)

    # leader side: list + resolve ----------------------------------

    def list_pending(self) -> list[PermissionRequest]:
        """Return every pending request, oldest-first by ``created_at``."""
        if not self.pending_dir.is_dir():
            return []
        out: list[PermissionRequest] = []
        for path in sorted(self.pending_dir.iterdir()):
            if not is_json_drop_file(path):
                continue
            req = try_load_json_file(path, PermissionRequest.from_dict)
            if req is not None:
                out.append(req)
        out.sort(key=lambda r: r.created_at)
        return out

    def read_pending(self, request_id: str) -> PermissionRequest | None:
        path = self._pending_path(request_id)
        if not path.is_file():
            return None
        return try_load_json_file(path, PermissionRequest.from_dict)

    def resolve(
        self,
        *,
        request_id: str,
        allowed: bool,
        reason: str = "",
        allow_once: bool = False,
    ) -> PermissionResponse:
        """Atomically write ``resolved/{id}.json`` and remove ``pending/{id}.json``.

        Raises:
            KeyError: if no pending request with ``request_id`` exists.
        """
        pending_path = self._pending_path(request_id)
        if not pending_path.is_file():
            raise KeyError(
                f"no pending permission request with id {request_id!r}"
            )

        response = PermissionResponse(
            request_id=request_id,
            allowed=bool(allowed),
            reason=reason,
            allow_once=bool(allow_once),
        )
        self.resolved_dir.mkdir(parents=True, exist_ok=True)
        save_json_file(
            self._resolved_path(request_id),
            response.to_dict(),
            trailing_newline=False,
        )
        # Resolved file is now on disk; remove pending. A failure here leaves
        # the pending file behind (leader retry is a KeyError because the
        # resolved file already exists — caller checks ``read_resolved``).
        with contextlib.suppress(OSError):
            pending_path.unlink()
        return response


# --- helpers ------------------------------------------------------
