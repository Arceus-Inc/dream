"""``BaseTool`` ABC, ``ToolDeclaration``, and metadata-derived ``Observation``.

Spec 05 slice A. These types stay internal to ``dream.tools``; the public
surface is the ``Tool`` / ``ToolResult`` / ``ToolContext`` Protocols in
``dream.contracts.tool``. ``BaseTool`` structurally satisfies the public
``Tool`` Protocol so external authors can either subclass for ergonomics or
implement the Protocol directly without coupling.

Spec invariants honoured here:

- Acceptance #6: every tool MUST declare ``risk`` + ``tier_required``;
  missing either is a session-blocking validation error raised at class
  creation time (``__init_subclass__``).
- Acceptance #7-8: ``Observation`` is built from ``ToolResult.is_error`` +
  ``ToolResult.metadata`` only. We NEVER parse ``ToolResult.content`` prose
  to decide status — that would re-introduce the "LLM-reads-LLM-text"
  ambiguity the typed-events rule forbids.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, get_args

from pydantic import BaseModel

if TYPE_CHECKING:
    from dream.contracts.tool import ToolResult
    from dream.tools._context import ToolExecutionContext


RiskClass = Literal["safe", "mutating", "external"]
"""Worst-case mutation profile of a tool, used for tier gating."""

ObservationStatus = Literal["success", "warning", "error"]


class ToolDeclarationError(ValueError):
    """A tool subclass is missing required declaration metadata."""


@dataclass(frozen=True)
class ToolDeclaration:
    """Per-tool capability + sandbox declaration.

    Every ``BaseTool`` subclass MUST set this. The engine reads ``risk`` for
    permission gating, ``tier_required`` to refuse calls that exceed the
    session's current sandbox tier, and ``timeout_seconds`` to bound runaway
    invocations (acceptance #5).
    """

    risk: RiskClass
    tier_required: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        valid = get_args(RiskClass)
        if self.risk not in valid:
            raise ToolDeclarationError(f"risk must be one of {valid}, got {self.risk!r}")
        if self.tier_required < 0:
            raise ToolDeclarationError(f"tier_required must be >= 0, got {self.tier_required}")
        if self.timeout_seconds <= 0:
            raise ToolDeclarationError(f"timeout_seconds must be > 0, got {self.timeout_seconds}")


@dataclass(frozen=True)
class Observation:
    """Engine-internal, structured view of a tool result.

    Built from ``ToolResult.is_error`` + ``ToolResult.metadata``. The
    summary references machine-readable facts; ``next_actions`` carries the
    error-recovery 3-part contract (root cause, safe retry, stop condition)
    surfaced from metadata when present.
    """

    status: ObservationStatus
    summary: str
    next_actions: tuple[str, ...]
    artifacts: tuple[str, ...]


def derive_observation(result: ToolResult) -> Observation:
    """Translate a ``ToolResult`` into an engine-internal ``Observation``.

    Status resolution (no content parsing):
        - ``is_error`` → ``"error"``
        - ``metadata["warning"]`` truthy → ``"warning"``
        - otherwise → ``"success"``

    The summary is composed from ``metadata`` facts when available
    (``returncode``, ``lines_changed``, ``bytes_written``, ``summary``).

    ``next_actions`` carries the error-recovery 3-part contract from
    metadata: ``root_cause``, ``safe_retry``, ``stop_condition``.

    ``artifacts`` collects ``metadata["artifacts"]`` (list of paths) plus
    ``metadata["offload_ref"]`` (a sidecar pointer) when present.
    """
    if result.is_error:
        status: ObservationStatus = "error"
    elif result.metadata.get("warning"):
        status = "warning"
    else:
        status = "success"

    summary = _build_summary(result, status=status)
    next_actions = _build_next_actions(result)
    artifacts = _build_artifacts(result)
    return Observation(
        status=status,
        summary=summary,
        next_actions=next_actions,
        artifacts=artifacts,
    )


def _build_summary(result: ToolResult, *, status: ObservationStatus) -> str:
    md = result.metadata
    if (explicit := md.get("summary")) is not None:
        return str(explicit)
    parts: list[str] = []
    for key in ("returncode", "lines_changed", "bytes_written", "files_matched"):
        if key in md:
            parts.append(f"{key}={md[key]}")
    if not parts:
        if status == "error":
            cause = md.get("root_cause", "unknown")
            return f"error: {cause}"
        if status == "warning":
            return "completed with warnings"
        return "ok"
    return ", ".join(parts)


def _build_next_actions(result: ToolResult) -> tuple[str, ...]:
    md = result.metadata
    out: list[str] = []
    for key in ("root_cause", "safe_retry", "stop_condition"):
        if (v := md.get(key)) is not None:
            out.append(f"{key}: {v}")
    explicit = md.get("next_actions")
    if isinstance(explicit, (list, tuple)):
        out.extend(str(x) for x in explicit)
    return tuple(out)


def _build_artifacts(result: ToolResult) -> tuple[str, ...]:
    md = result.metadata
    out: list[str] = []
    raw = md.get("artifacts")
    if isinstance(raw, (list, tuple)):
        out.extend(str(x) for x in raw)
    if (ptr := md.get("offload_ref")) is not None:
        out.append(str(ptr))
    return tuple(out)


class BaseTool(ABC):
    """ABC for default + per-repo tools that want validation + schema sugar.

    Subclasses MUST set, at class scope:

    - ``name``: unique tool identifier (lowercase + underscore).
    - ``description``: short, model-facing description.
    - ``declaration``: a ``ToolDeclaration``.
    - ``input_model``: a ``pydantic.BaseModel`` subclass describing input.

    ``__init_subclass__`` enforces this at class-creation time so
    registration cannot silently accept a half-declared tool. (Acceptance
    #6: validation is session-blocking; with the lift here, it is in fact
    *import-time* blocking — even better.)

    The class structurally satisfies the public ``Tool`` Protocol, so
    external authors who prefer pure-Protocol implementations are not
    forced to subclass.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    declaration: ClassVar[ToolDeclaration]
    input_model: ClassVar[type[BaseModel]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Only validate concrete tool classes that ship their own ``execute``
        # (or have had it patched in). Abstract intermediate layers — those
        # that leave ``execute`` as the inherited ``@abstractmethod`` — are
        # skipped so test fixtures and abstract base layers can declare
        # partial state without tripping the gate.
        if "execute" not in cls.__dict__:
            return
        missing: list[str] = []
        for attr in ("name", "description", "declaration", "input_model"):
            if not hasattr(cls, attr) or getattr(cls, attr, None) is None:
                missing.append(attr)
        if missing:
            raise ToolDeclarationError(
                f"{cls.__name__}: missing required class attribute(s): {', '.join(missing)}"
            )
        if not isinstance(cls.declaration, ToolDeclaration):
            raise ToolDeclarationError(
                f"{cls.__name__}: declaration must be a ToolDeclaration "
                f"instance, got {type(cls.declaration).__name__}"
            )

    @abstractmethod
    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        """Run the tool. Subclasses must implement."""

    def input_schema(self) -> dict[str, Any]:
        """Return the JSON Schema for this tool's input."""
        return self.input_model.model_json_schema()

    def is_read_only(self) -> bool:
        """Worst-case read-only flag, derived from declared ``risk``."""
        return self.declaration.risk == "safe"

    def is_read_only_for(self, input: dict[str, Any]) -> bool:
        """Per-call read-only refinement.

        Defaults to ``is_read_only()``; tools may override to downclassify a
        specific invocation (e.g. ``bash`` running ``cat foo`` is read-only
        even though the tool itself is mutating).
        """
        del input
        return self.is_read_only()

    def to_api_schema(self) -> dict[str, Any]:
        """Return the schema in API shape (Anthropic / OpenAI tools schema)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
        }


__all__ = [
    "BaseTool",
    "Observation",
    "ObservationStatus",
    "RiskClass",
    "ToolDeclaration",
    "ToolDeclarationError",
    "derive_observation",
]
