"""Bundled default manifests for the three canonical roles.

Operators override per-field via ``.harness/roles/{role}.toml`` (see
:mod:`dream.roles._loader`); these are the floor.

- planner: read-only triplet, no writers, no shell; ``permission_mode="plan"``
  so any accidental side effect is deflected to an explicit plan output.
- generator: ``tools=None`` (= all registered tools, intersected with the
  active sandbox tier at #13); ``permission_mode="default"``.
- evaluator: reads + ``bash`` for in-session verify (Hermes/CC shape); no
  writers and no ``spawn_subagent``. No harness oracle sidecar.
"""

from __future__ import annotations

from dream.roles._manifest import RoleManifest, RoleName

# The read-only triplet — every read tool a planner may use without any tier
# requirement above READ_ONLY. Tools absent from the registry are silently
# dropped by ``compute_minimum_toolset``, so listing extras here (e.g.
# ``query_logs`` from #12) is safe before those tools land.
_READ_ONLY_TRIPLET: tuple[str, ...] = (
    "read_file",
    "git",
    "query_logs",
)

# Evaluator: same reads + bash so verification runs inside the judge session.
_EVALUATOR_TOOLS: tuple[str, ...] = (
    "read_file",
    "git",
    "query_logs",
    "bash",
)

_WRITERS_DENIED: tuple[str, ...] = (
    "write_file",
    "edit_file",
    "bash",
)

# Writers only — spawn_subagent is absent from the default registry (added when
# subagents are enabled); keep it out of the allow-list via tools= explicit.
_EVALUATOR_DENIED: tuple[str, ...] = (
    "write_file",
    "edit_file",
)


_PLANNER = RoleManifest(
    name="planner",
    description="Breaks an intent into a sprint contract before any code changes.",
    system_prompt=(
        "You are the planner. Read the brief, the ledger, and the relevant code; "
        "produce the sprint contract under docs/exec-plans/active. Do not modify "
        "source files. If you need a capability you do not have, emit a "
        "request_capability event rather than guessing."
    ),
    tools=_READ_ONLY_TRIPLET,
    disallowed_tools=_WRITERS_DENIED,
    permission_mode="plan",
    effort="medium",
    color="blue",
)


_GENERATOR = RoleManifest(
    name="generator",
    description="Executes the sprint contract in the worktree.",
    system_prompt=(
        "You are the generator. Follow the sprint contract verbatim. "
        "Make the smallest change that satisfies every acceptance criterion. "
        "Run the verification steps before declaring done."
    ),
    tools=None,
    permission_mode="default",
    effort="medium",
    color="green",
)


_EVALUATOR = RoleManifest(
    name="evaluator",
    description="Verifies the generator's output against the sprint contract.",
    system_prompt=(
        "You are the evaluator. Read code and artefacts, run verification via "
        "bash yourself, and judge the contract. You may not modify source files "
        "or spawn subagents. Produce a verification report with pass/fail per "
        "acceptance criterion."
    ),
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
