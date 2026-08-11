"""Bundled default manifests for the three canonical roles.

Operators override per-field via ``.harness/roles/{role}.toml`` (see
:mod:`dream.roles._loader`); these are the floor.

- planner: read-only triplet, no writers, no shell; ``permission_mode="plan"``
  so any accidental side effect is deflected to an explicit plan output.
- generator: ``tools=None`` (= all registered tools, intersected with the
  active sandbox tier at #13); ``permission_mode="default"``.
- evaluator: reads + ``bash`` for in-session verify (Hermes/CC shape) plus
  ``query_logs`` over session traces; no writers and no ``spawn_subagent``.
"""

from __future__ import annotations

from dream.roles._manifest import RoleManifest, RoleName

# The read-only Level-2 set — every read tool a planner may use without any
# tier requirement above READ_ONLY. Pack-only tools (e.g. ``query_logs``) stay
# out of the default manifest so unknown names are never silently dropped.
_READ_ONLY_TRIPLET: tuple[str, ...] = (
    "read_file",
    "git",
    "grep",
    "glob",
)

# Evaluator: reads + bash verify + session-trace query (Spec 12b). ``query_logs``
# lives in the observability pack, which ``build_harness`` enables by default.
_EVALUATOR_TOOLS: tuple[str, ...] = (
    "read_file",
    "git",
    "grep",
    "glob",
    "bash",
    "query_logs",
)

_WRITERS_DENIED: tuple[str, ...] = (
    "write_file",
    "apply_patch",
    "bash",
)

# Writers only — spawn_subagent is absent from the default registry (added when
# subagents are enabled); keep it out of the allow-list via tools= explicit.
_EVALUATOR_DENIED: tuple[str, ...] = (
    "write_file",
    "apply_patch",
)


# Phase identity + protocol live in packaged standing orders
# (``dream.prompts.standing_orders``). Manifests own tools / permission_mode.
_PLANNER = RoleManifest(
    name="planner",
    description="Breaks an intent into a sprint contract before any code changes.",
    system_prompt="",
    tools=_READ_ONLY_TRIPLET,
    disallowed_tools=_WRITERS_DENIED,
    permission_mode="plan",
    effort="medium",
    color="blue",
)


_GENERATOR = RoleManifest(
    name="generator",
    description="Executes the sprint contract in the worktree.",
    system_prompt="",
    tools=None,
    permission_mode="default",
    effort="medium",
    color="green",
)


_EVALUATOR = RoleManifest(
    name="evaluator",
    description="Verifies the generator's output against the sprint contract.",
    system_prompt="",
    tools=_EVALUATOR_TOOLS,
    disallowed_tools=_EVALUATOR_DENIED,
    permission_mode="default",
    effort="medium",
    color="magenta",
)


_DEFAULTS: dict[str, RoleManifest] = {
    "planner": _PLANNER,
    "generator": _GENERATOR,
    "evaluator": _EVALUATOR,
}


def default_role_manifest(name: RoleName) -> RoleManifest:
    """Return the bundled default manifest for ``name``.

    Raises ``ValueError`` for unknown names so the typo path is loud.
    """
    try:
        return _DEFAULTS[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown role {name!r}; expected one of {sorted(_DEFAULTS)}"
        ) from exc
