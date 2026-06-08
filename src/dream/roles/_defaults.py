"""Bundled default manifests for the three canonical roles.

Operators override per-field via ``.harness/roles/{role}.toml`` (see
:mod:`dream.roles._loader`); these are the floor.

- planner: read-only triplet, no writers, no shell; ``permission_mode="plan"``
  so any accidental side effect is deflected to an explicit plan output.
- generator: ``tools=None`` (= all registered tools, intersected with the
  active sandbox tier at #13); ``permission_mode="default"``.
- evaluator: read-only triplet (no writers, no shell); contract-named
  verifiers may be added at spawn time as an explicit list, since #12 owns
  the verifier registry.
"""

from __future__ import annotations

from dream.roles._manifest import RoleManifest, RoleName

# The read-only triplet — every read tool a planner/evaluator may use without
# any tier requirement above READ_ONLY. Tools absent from the registry are
# silently dropped by ``compute_minimum_toolset``, so listing extras here
# (e.g. ``query_logs`` from #12) is safe before those tools land.
_READ_ONLY_TRIPLET: tuple[str, ...] = (
    "file_read",
    "git",
    "query_logs",
)

_WRITERS_DENIED: tuple[str, ...] = (
    "file_write",
    "file_edit",
    "bash",
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
        "You are the evaluator. You may read code, logs, and prior artefacts, "
        "and run the contract-named verifiers. You may not modify source files. "
        "Produce a verification report with pass/fail per acceptance criterion."
    ),
    tools=_READ_ONLY_TRIPLET,
    disallowed_tools=_WRITERS_DENIED,
    permission_mode="plan",
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
