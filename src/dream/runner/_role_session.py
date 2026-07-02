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

from dream.events import Error, Event, TextDelta, ToolUseResult, ToolUseStart
from dream.roles import (
    RoleManifest,
    RoleName,
    default_role_manifest,
    load_role_manifest,
)
from dream.runner._observer import RunTaskObserver
from dream.session import SessionCost, SessionOptions

if TYPE_CHECKING:
    from dream.harness import Harness

__all__ = [
    "ROLE_MANIFEST_METADATA_KEY",
    "ROLE_NAME_METADATA_KEY",
    "RoleSessionError",
    "RunRoleResult",
    "resolve_role_manifest",
    "run_role",
]


# Stable keys a role-aware engine factory reads off ``SessionOptions.metadata``.
# Namespaced so plugin metadata can't collide.
ROLE_NAME_METADATA_KEY = "dream.role"
ROLE_MANIFEST_METADATA_KEY = "dream.role_manifest"


class RoleSessionError(RuntimeError):
    """Raised when a role-bound session errored mid-stream."""


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
        metadata=metadata,
    )

    session = await harness.start_session(effective)

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
    )
