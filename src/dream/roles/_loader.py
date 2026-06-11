"""Layered loader for role manifests.

Resolution order, per spec 10 §Artefact shapes:

1. Bundled default for the role (see :mod:`dream.roles._defaults`).
2. Project overlay at ``{harness_dir}/roles/{role}.toml`` — per-field
   replacement on top of the default.

The loader is read-only: missing overlay paths are silently treated as
"no override". Invalid overlay values surface as a pydantic
``ValidationError`` via the merged construction call.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from dream.roles._defaults import default_role_manifest
from dream.roles._manifest import RoleManifest, RoleName


def load_role_manifest(role: RoleName, *, harness_dir: Path) -> RoleManifest:
    """Resolve the effective manifest for ``role`` under ``harness_dir``.

    ``harness_dir`` is the in-repo ``.harness`` directory (the operator-owned
    config root); the overlay lives at ``harness_dir / "roles" / f"{role}.toml"``.

    Raises ``ValueError`` for ``"subagent"``: subagent manifests are always
    synthesised at spawn time, never loaded from disk. This guard prevents
    operators from accidentally placing a ``subagent.toml`` overlay file and
    expecting it to be picked up.
    """
    if role == "subagent":
        raise ValueError(
            "role 'subagent' is synthesized at spawn time and cannot be "
            "loaded by name; the spawn_subagent tool builds it at runtime"
        )
    base = default_role_manifest(role)
    overlay_path = harness_dir / "roles" / f"{role}.toml"
    if not overlay_path.is_file():
        return base

    # ``overlay`` is the parsed ``{role}.toml`` table: a partial
    # ``RoleManifest`` keyed by field name (e.g. ``{"tools": [...],
    # "model": "...", "system_prompt": "..."}``) — any subset of the
    # manifest's fields, each replacing the bundled default wholesale.
    with overlay_path.open("rb") as handle:
        overlay: dict[str, Any] = tomllib.load(handle)

    merged = base.model_dump()
    for field, value in overlay.items():
        merged[field] = value
    # Name is fixed by which file we loaded; an overlay cannot rename a role.
    merged["name"] = role
    return RoleManifest.model_validate(merged)
