"""Role manifests + capability minimisation (Spec 10 slice A).

A *role* is an ``AgentDefinition``-shaped contract that pins what one bounded
subagent is allowed to do: name + system prompt + tool allow-/deny-lists +
skills + mcp servers + permission mode + isolation + memory scope + effort +
color. Three roles are canonical — ``planner``, ``generator``, ``evaluator``;
operators may overlay per-field overrides at ``.harness/roles/{role}.toml``.

The minimum toolset is computed from the manifest and the active sandbox
tier; it can be *narrower* than the manifest but never wider. A role
cannot widen itself mid-session: the only escalation path is the
recordable :class:`RequestCapabilityEvent`, which a parent runner may
choose to act on by re-spawning with a different manifest.
"""

from __future__ import annotations

from dream.roles._defaults import default_role_manifest
from dream.roles._events import RequestCapabilityEvent, request_capability
from dream.roles._loader import load_role_manifest
from dream.roles._manifest import RoleManifest, RoleName
from dream.roles._toolset import compute_minimum_toolset

__all__ = [
    "RequestCapabilityEvent",
    "RoleManifest",
    "RoleName",
    "compute_minimum_toolset",
    "default_role_manifest",
    "load_role_manifest",
    "request_capability",
]
