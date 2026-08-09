"""Role-bound session execution: spec 10 slice G2.

The ``run_role`` helper drives one session as a named role: it resolves
the manifest (bundled default; overlay-merged from
``{harness_dir}/roles/{role}.toml`` when given), combines the role's
system prompt with the caller's, marks the resolved manifest on
``SessionOptions.metadata`` so a role-aware engine factory can apply
capability minimisation and pick the permission mode, then drains the
session to completion and returns the assistant text + cost.

This is the primitive the production planner / generator / evaluator
heads compose into :func:`dream.runner.run_task` — without it, each
head would have to re-implement the session-wiring boilerplate.

The helper is intentionally engine-agnostic: it talks to ``Harness``
through :meth:`Harness.start_session` only. The decision of *whether*
to enforce the role's allow-list at dispatch time is left to whichever
engine factory the harness was configured with — when that factory
reads :data:`ROLE_MANIFEST_METADATA_KEY` it can intersect the tool
registry with :func:`dream.roles.compute_minimum_toolset`; when it
ignores the key the manifest is enforced via the role's system prompt
discipline only (the v1 contract every role is built around).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dream.errors import SessionResumeError
from dream.events import Error, Event, TextDelta, ToolUseResult, ToolUseStart
from dream.roles import (
    RoleManifest,
    RoleName,
    default_role_manifest,
    load_role_manifest,
)
from dream.runner._observer import RunTaskObserver
from dream.services.session_store import SessionHandle, checked_session_id
from dream.session import Session, SessionCost, SessionOptions

if TYPE_CHECKING:
    from dream.harness import Harness

__all__ = [
    "ROLE_MANIFEST_METADATA_KEY",
    "ROLE_NAME_METADATA_KEY",
    "RoleSessionError",
    "RunRoleResult",
    "resolve_role_manifest",
    "role_session_id",
    "run_role",
]


# Stable keys a role-aware engine factory reads off ``SessionOptions.metadata``.
# Namespaced so plugin metadata can't collide.
ROLE_NAME_METADATA_KEY = "dream.role"
ROLE_MANIFEST_METADATA_KEY = "dream.role_manifest"


class RoleSessionError(RuntimeError):
    """Raised when a role-bound session errored mid-stream."""


def role_session_id(scope: str, role: RoleName | str) -> str:
    """Name ``role``'s thread inside ``scope``.

    One scope key per task gives every role its own resumable thread — a
    planner and an evaluator are different conversations and must not share
    one. Heads bound to the same role deliberately land on the same thread.

    The separator is a hyphen because a session id becomes a directory name
    under the sidecar root, and ``:`` is rejected there as Windows drive and
    alternate-data-stream syntax.
    """
    return checked_session_id(f"{scope}-{role}")


@dataclass(frozen=True)
class RunRoleResult:
    """The product of one role-bound session.

    ``final_text`` is the concatenation of every ``TextDelta`` the
    assistant emitted across the whole session — the seam the production
    heads parse for structured artefacts (planner ledger JSON, evaluator
    verdict). ``events`` keeps the full public event stream so callers
    that want richer signal (tool calls, compaction, cost per turn) can
    walk it without re-running the session.
    """

    role: RoleName
    session_id: str
    final_text: str
    cost: SessionCost
    events: tuple[Event, ...]
    # The pointer to resume this role thread later — ``None`` unless the
    # caller named the session via ``session_id``.
    session_handle: SessionHandle | None = None


def resolve_role_manifest(
    role: RoleName | RoleManifest,
    *,
    harness_dir: Path | None = None,
) -> RoleManifest:
    """Pick the effective manifest for ``role``.

    Passing a :class:`RoleManifest` returns it unchanged (the caller has
    already done resolution). Passing a name uses the layered loader if
    ``harness_dir`` is set, else the bundled default — the same rule
    spec 10 §Artefact shapes pins for every role-spawning seam.
    """
    if isinstance(role, RoleManifest):
        return role
    if harness_dir is not None:
        return load_role_manifest(role, harness_dir=harness_dir)
    return default_role_manifest(role)


def _combine_system_prompts(manifest: RoleManifest, caller: str | None) -> str:
    """Manifest prompt first, caller addendum second.

    ``system_prompt_mode`` is honoured by the role-aware engine factory
    (it decides whether to drop its standing orders); here we just lay
    the manifest text down before the per-call addendum so the
    role-locked discipline always reaches the model.
    """
    body = manifest.system_prompt
    if caller:
        return f"{body}\n\n{caller}"
    return body


async def run_role(
    harness: Harness,
    role: RoleName | RoleManifest,
    intent: str,
    *,
    options: SessionOptions | None = None,
    harness_dir: Path | None = None,
    observer: RunTaskObserver | None = None,
    session_id: str | None = None,
) -> RunRoleResult:
    """Run one session as ``role``; return assistant text + cost.

    Resolves the manifest, prepends its system prompt to
    ``options.system_prompt``, marks the manifest on
    ``SessionOptions.metadata`` (keys :data:`ROLE_NAME_METADATA_KEY` /
    :data:`ROLE_MANIFEST_METADATA_KEY`), opens a session via
    ``harness.start_session``, drains ``session.send(intent)`` to
    completion, and surfaces the result. The session is closed even on
    the error path. An ``events.Error`` mid-stream becomes a
    :class:`RoleSessionError`.

    When ``observer`` is supplied, every mid-stream event is mirrored
    to the observer in real time: ``role.session.opened`` /
    ``role.session.closed`` bracket the session, with ``role.text``,
    ``role.tool.start``, ``role.tool.result`` and ``role.error`` events
    in between. Production heads forward the observer through this
    kwarg so :func:`dream.runner.run_task` can drive a live walkthrough.

    ``session_id`` names the role thread so it survives the process: the
    session resumes that snapshot when one is readable, and the run's
    :class:`SessionHandle` comes back on the result. A spent snapshot (never
    written, or corrupt) starts the thread over under the same name rather
    than failing the run, so the caller keeps one stable key. A snapshot taken
    under another working directory is not spent — it stays where it is, this
    run gets a fresh unnamed session, and the result carries no handle.
    Without ``session_id``, nothing is persisted.
    """
    manifest = resolve_role_manifest(role, harness_dir=harness_dir)
    base = options if options is not None else SessionOptions()

    # Copy the caller's metadata dict before mutating: ``SessionOptions``
    # is frozen, but ``field(default_factory=dict)`` means the dict itself
    # is shared by reference, and a caller may legitimately keep the
    # original around after handing it to us.
    metadata = dict(base.metadata)
    metadata[ROLE_NAME_METADATA_KEY] = manifest.name
    metadata[ROLE_MANIFEST_METADATA_KEY] = manifest
    # Stash the observer so the spawn tool can forward it into a child session (depth-2 visibility):
    # a nested spawn's events then reach this same observer/bus instead of the child's isolated
    # stream. A child run_role re-stashes it, so a grandchild inherits it too.
    if observer is not None:
        from dream.tools.builtin.spawn_subagent import OBSERVER_KEY

        metadata[OBSERVER_KEY] = observer

    effective = SessionOptions(
        model=base.model,
        system_prompt=_combine_system_prompts(manifest, base.system_prompt),
        max_turns=base.max_turns,
        response_format=base.response_format,
        metadata=metadata,
    )

    session, owns_session_id = await _open_role_session(harness, effective, session_id)

    role_label = str(manifest.name)

    def _emit(event: dict[str, Any]) -> None:
        if observer is not None:
            observer.on_event(event)

    _emit(
        {
            "kind": "role.session.opened",
            "role": role_label,
            "session_id": session.id,
        }
    )

    text_chunks: list[str] = []
    captured: list[Event] = []
    error: Error | None = None
    try:
        stream: AsyncIterator[Event] = session.send(intent)
        async for ev in stream:
            captured.append(ev)
            if isinstance(ev, TextDelta):
                text_chunks.append(ev.text)
                _emit({"kind": "role.text", "role": role_label, "text": ev.text})
            elif isinstance(ev, ToolUseStart):
                _emit(
                    {
                        "kind": "role.tool.start",
                        "role": role_label,
                        "tool": ev.name,
                        "input": dict(ev.input),
                    }
                )
            elif isinstance(ev, ToolUseResult):
                _emit(
                    {
                        "kind": "role.tool.result",
                        "role": role_label,
                        "tool": ev.name,
                        "is_error": ev.is_error,
                        "content": ev.content,
                        "content_preview": ev.content[:240],
                    }
                )
            # First error wins; the engine may surface a follow-up
            # ``TurnComplete`` with empty blocks but the diagnosis
            # belongs to the first failure.
            elif isinstance(ev, Error) and error is None:
                error = ev
                _emit(
                    {
                        "kind": "role.error",
                        "role": role_label,
                        "message": ev.message,
                    }
                )
    finally:
        await session.close()
        _emit(
            {
                "kind": "role.session.closed",
                "role": role_label,
                "session_id": session.id,
                "model": session.model,
                "usage": {
                    "input_tokens": session.cost.input_tokens,
                    "output_tokens": session.cost.output_tokens,
                    "cache_read_tokens": session.cost.cache_read_tokens,
                    "cache_write_tokens": session.cost.cache_write_tokens,
                },
                "cost_usd": session.cost.cost_usd,
            }
        )

    # Persist before the error check: a session that errored mid-stream still
    # holds the history explaining why, which the next run of this thread
    # should see. A hard crash (an exception, not an ``Error`` event) skips
    # this and leaves the previous snapshot standing.
    handle = await harness.save_session(session) if owns_session_id else None

    if error is not None:
        raise RoleSessionError(
            f"role {manifest.name!r} session {session.id} errored: {error.message}"
        )

    return RunRoleResult(
        role=manifest.name,
        session_id=session.id,
        final_text="".join(text_chunks),
        cost=session.cost,
        events=tuple(captured),
        session_handle=handle,
    )


async def _open_role_session(
    harness: Harness,
    options: SessionOptions,
    session_id: str | None,
) -> tuple[Session, bool]:
    """Open the named role thread; say whether this run may save under it.

    Returns the session and whether it owns ``session_id``. Only a run that
    owns the name is allowed to write a snapshot there.
    """
    if session_id is None:
        return await harness.start_session(options), False
    try:
        return await harness.resume_session(session_id, options=options), True
    except SessionResumeError as exc:
        if not exc.should_clear_handle:
            # A working-directory mismatch leaves the snapshot intact and still
            # resumable from the workspace that wrote it. Run the role on an
            # anonymous session so finishing here can't save over it.
            return await harness.start_session(options), False
        # One retry with a clean thread, the same fallback a coding CLI makes
        # when ``--resume`` is refused: losing continuity beats stranding the
        # role. Drop the spent snapshot so the name is free and later runs
        # don't re-pay the failure.
        await harness.reset_session(session_id)
        return await harness.start_session(options, session_id=session_id), True
