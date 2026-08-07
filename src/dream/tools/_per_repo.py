"""Per-repo tools from ``.harness/tools/{name}.toml`` (Spec 05).

Discovers TOML declarations at harness build / session start, validates the
strict schema (``name`` / ``description`` / ``command`` / ``parameters`` /
``risk`` / ``tier_required`` / ``timeout_seconds``), synthesizes a
:class:`BaseTool` that runs the command template, and registers it under
:attr:`ToolSource.PER_REPO`. A per-repo tool may shadow a default by name;
the caller receives a warning string for each shadow.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, RiskClass, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin._errors import tool_error
from dream.tools.builtin.mcp_tool import input_model_from_schema

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class PerRepoToolError(ValueError):
    """One or more per-repo tool declarations failed validation."""

    def __init__(self, findings: tuple[str, ...]) -> None:
        self.findings = findings
        super().__init__("; ".join(findings))


@dataclass(frozen=True)
class PerRepoLoadResult:
    """Outcome of loading ``.harness/tools/`` into a registry."""

    registered: tuple[str, ...]
    warnings: tuple[str, ...]


class _PerRepoDeclaration(BaseModel):
    """Strict Spec 05 per-repo tool declaration (TOML → model)."""

    name: str
    description: str
    command: str
    parameters: dict[str, object] | str = Field(default_factory=dict)
    returns: str = ""
    risk: RiskClass
    tier_required: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(
                f"name must be lowercase snake_case starting with a letter, got {value!r}"
            )
        return value

    @field_validator("command")
    @classmethod
    def _nonempty_command(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command must be non-empty")
        return value


class _PerRepoPlaceholderInput(BaseModel):
    """Placeholder so the adapter class passes BaseTool class validation."""


class PerRepoCommandTool(BaseTool):
    """Run a shell-style command template declared in ``.harness/tools/``."""

    name = "per_repo_placeholder"
    description = "A per-repo declared tool."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=60.0)
    input_model: type[BaseModel] = _PerRepoPlaceholderInput

    def __init__(self, decl: _PerRepoDeclaration, *, parameters: dict[str, object]) -> None:
        self._command = decl.command
        self._returns = decl.returns
        self.name = decl.name
        self.description = decl.description
        self.input_model = input_model_from_schema(decl.name, parameters)
        self.declaration = ToolDeclaration(
            risk=decl.risk,
            tier_required=decl.tier_required,
            timeout_seconds=decl.timeout_seconds,
        )

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        del input
        # Command templates are shell-backed; treat as mutating when declared so.
        return ToolEffects(command=self._command)

    def is_read_only_for(self, input: dict[str, Any]) -> bool:
        del input
        return self.declaration.risk == "safe"

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        try:
            argv_command = _format_command(self._command, input)
        except KeyError as exc:
            return tool_error(
                f"Missing argument for command template: {exc.args[0]}",
                root_cause=f"placeholder {exc.args[0]!r} not provided",
                safe_retry="pass every placeholder named in the command template",
                stop_condition="do not retry with the same missing argument",
            )
        except ValueError as exc:
            return tool_error(
                str(exc),
                root_cause=str(exc),
                safe_retry="fix the tool's command template or arguments",
                stop_condition="do not retry until the template is valid",
            )

        result = await ctx.run_subprocess(
            ["bash", "-lc", argv_command],
            cwd=ctx.working_dir,
            timeout=self.declaration.timeout_seconds,
        )
        if self._returns and not result.is_error:
            meta = dict(result.metadata)
            meta["returns_hint"] = self._returns
            return ToolResult(
                content=result.content,
                structured=result.structured,
                is_error=result.is_error,
                metadata=meta,
            )
        return result


def load_per_repo_tools(registry: ToolRegistry, tools_dir: Path) -> PerRepoLoadResult:
    """Discover, validate, and register every ``*.toml`` under ``tools_dir``.

    Missing or empty ``tools_dir`` is a no-op. Invalid declarations raise
    :class:`PerRepoToolError` (session-blocking). Shadows of an already-
    registered name are allowed and reported in ``warnings``.
    """
    if not tools_dir.is_dir():
        return PerRepoLoadResult(registered=(), warnings=())

    findings: list[str] = []
    registered: list[str] = []
    warnings: list[str] = []
    declarations: list[tuple[Path, _PerRepoDeclaration, PerRepoCommandTool]] = []
    names: set[str] = set()

    for path in sorted(tools_dir.glob("*.toml")):
        try:
            decl, parameters = _load_declaration(path)
        except PerRepoToolError as exc:
            findings.extend(exc.findings)
            continue
        except (OSError, tomllib.TOMLDecodeError, ValidationError, ValueError) as exc:
            findings.append(f"tool declaration invalid ({path.name}): {exc}")
            continue

        if decl.name in names:
            findings.append(f"duplicate per-repo tool name: {decl.name!r}")
            continue
        names.add(decl.name)
        try:
            tool = PerRepoCommandTool(decl, parameters=parameters)
        except (TypeError, ValueError, ValidationError) as exc:
            findings.append(f"tool declaration invalid ({path.name}): {exc}")
            continue
        declarations.append((path, decl, tool))

    if findings:
        raise PerRepoToolError(tuple(findings))

    for path, decl, tool in declarations:
        # Stem should match declared name (operator ergonomics); mismatch warns.
        if path.stem != decl.name:
            warnings.append(
                f"per-repo tool file {path.name!r} declares name={decl.name!r} "
                f"(stem mismatch; using declared name)"
            )
        prior = registry.register(tool, source=ToolSource.PER_REPO, replace=True)
        if prior is not None:
            warnings.append(
                f"per-repo tool {decl.name!r} shadows {prior.value} tool of the same name"
            )
        registered.append(decl.name)

    return PerRepoLoadResult(registered=tuple(registered), warnings=tuple(warnings))


def _load_declaration(path: Path) -> tuple[_PerRepoDeclaration, dict[str, object]]:
    """Parse one TOML file into a validated declaration + JSON Schema dict."""
    with path.open("rb") as handle:
        raw: Mapping[str, object] = tomllib.load(handle)

    # Missing risk / tier_required must be blocking (Spec 05 acceptance #6/#18).
    missing: list[str] = []
    if "risk" not in raw:
        missing.append("risk")
    if "tier_required" not in raw:
        missing.append("tier_required")
    if missing:
        raise PerRepoToolError(
            (f"tool declaration missing {', '.join(missing)}: {path.stem}",)
        )

    decl = _PerRepoDeclaration.model_validate(dict(raw))
    parameters = _resolve_parameters(decl.parameters, tools_dir=path.parent)
    schema_type = parameters.get("type")
    properties = parameters.get("properties")
    if schema_type not in (None, "object"):
        raise PerRepoToolError(
            (f"tool declaration {decl.name!r}: parameters must be a JSON object schema",)
        )
    if "type" not in parameters:
        parameters = {"type": "object", **parameters}
    properties = parameters.get("properties", {})
    if not isinstance(properties, Mapping):
        raise PerRepoToolError(
            (f"tool declaration {decl.name!r}: parameters.properties must be a mapping",)
        )
    unknown = _command_placeholders(decl.command) - {str(name) for name in properties}
    if unknown:
        unknown_names = ", ".join(sorted(unknown))
        raise PerRepoToolError(
            (
                f"tool declaration {decl.name!r}: "
                f"unknown command placeholder(s): {unknown_names}",
            )
        )
    return decl, parameters


def _command_placeholders(command: str) -> set[str]:
    """Return field names referenced by a command template."""
    fields: set[str] = set()
    for _, field_name, _, _ in Formatter().parse(command):
        if field_name is not None:
            fields.add(field_name)
    return fields


def _resolve_parameters(
    parameters: dict[str, object] | str, *, tools_dir: Path
) -> dict[str, object]:
    """Inline schema dict, or ``$ref`` / path string relative to ``tools_dir``."""
    if isinstance(parameters, str):
        ref_path = (tools_dir / parameters).resolve()
        if not str(ref_path).startswith(str(tools_dir.resolve())):
            raise ValueError(f"parameters $ref escapes tools dir: {parameters}")
        if not ref_path.is_file():
            raise ValueError(f"parameters $ref not found: {parameters}")
        import json

        loaded = json.loads(ref_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"parameters $ref must be a JSON object: {parameters}")
        return {str(k): v for k, v in loaded.items()}

    # Support ``parameters = { $ref = "schema.json" }`` TOML form.
    ref = parameters.get("$ref")
    if isinstance(ref, str) and len(parameters) == 1:
        return _resolve_parameters(ref, tools_dir=tools_dir)
    return dict(parameters)


def _format_command(template: str, args: Mapping[str, object]) -> str:
    """Substitute ``{name}`` placeholders; shell-quote every substituted value."""
    # Reject attribute/index formats so ``{foo.bar}`` cannot escape quoting.
    for _, field_name, format_spec, conversion in Formatter().parse(template):
        if field_name is None:
            continue
        if format_spec or conversion or "." in field_name or "[" in field_name:
            raise ValueError(
                f"unsupported placeholder syntax in command template: {{{field_name}}}"
            )
        if field_name not in args or args[field_name] is None:
            raise KeyError(field_name)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return shlex.quote(str(args[key]))

    return _PLACEHOLDER_RE.sub(_replace, template)


__all__ = [
    "PerRepoCommandTool",
    "PerRepoLoadResult",
    "PerRepoToolError",
    "load_per_repo_tools",
]
