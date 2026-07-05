"""Default ``lsp`` tool — read-only Python code intelligence (tier 0, safe).

Ported from OpenHarness ``lsp_tool.py`` onto dream's contract. Backed by the
:mod:`dream.services.lsp` AST walker — no language-server process. The target
file is confined to the working directory; every operation is read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from dream.contracts.tool import ToolResult
from dream.services.lsp import (
    SymbolLocation,
    find_references,
    go_to_definition,
    hover,
    list_document_symbols,
    workspace_symbol_search,
)
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._paths import PathEscapesRoot, resolve_within
from dream.tools.builtin._errors import tool_error

_Operation = Literal[
    "document_symbol",
    "workspace_symbol",
    "go_to_definition",
    "find_references",
    "hover",
]


class LspInput(BaseModel):
    """Arguments for a code-intelligence query."""

    operation: _Operation = Field(description="The code-intelligence operation to perform.")
    file_path: str | None = Field(
        default=None, description="Source file, within the working directory."
    )
    symbol: str | None = Field(default=None, description="Explicit symbol name to look up.")
    line: int | None = Field(default=None, ge=1, description="1-based line for position lookups.")
    character: int | None = Field(
        default=None, ge=1, description="1-based character offset for position lookups."
    )
    query: str | None = Field(default=None, description="Substring query for workspace_symbol.")

    @model_validator(mode="after")
    def _validate(self) -> LspInput:
        if self.operation == "workspace_symbol":
            if not self.query:
                raise ValueError("workspace_symbol requires query")
            return self
        if not self.file_path:
            raise ValueError(f"{self.operation} requires file_path")
        if self.operation == "document_symbol":
            return self
        if not self.symbol and self.line is None:
            raise ValueError(f"{self.operation} requires symbol or line")
        return self


class LspTool(BaseTool):
    """Read-only code intelligence for Python source files."""

    name = "lsp"
    description = (
        "Inspect Python code: document/workspace symbols, go-to-definition, "
        "find-references, and hover, across the current workspace."
    )
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=15.0)
    input_model = LspInput

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        path = input.get("file_path")
        return ToolEffects(target_paths=(Path(str(path)),)) if path else ToolEffects()

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = LspInput.model_validate(input)
        root = Path(ctx.working_dir).resolve()

        if args.operation == "workspace_symbol":
            results = workspace_symbol_search(root, args.query or "")
            return ToolResult(
                content=_format_symbols(results, root),
                metadata={"result_count": len(results), "summary": f"{len(results)} symbol(s)"},
            )

        assert args.file_path is not None  # guaranteed by the validator
        try:
            file_path = resolve_within(root, args.file_path)
        except PathEscapesRoot as exc:
            return tool_error(
                f"Path outside the working directory: {args.file_path}",
                root_cause=str(exc),
                safe_retry="pass a file path within the working directory",
                stop_condition="do not retry with the same out-of-tree path",
            )
        if not file_path.exists():
            return tool_error(
                f"File not found: {file_path}",
                root_cause=f"path does not exist: {file_path}",
                safe_retry="verify the file path and retry",
                stop_condition="do not retry with the same missing path",
            )
        if file_path.suffix != ".py":
            return tool_error(
                "The lsp tool currently supports Python (.py) files only.",
                root_cause=f"unsupported suffix: {file_path.suffix or '(none)'}",
                safe_retry="pass a .py file",
                stop_condition="do not retry with a non-Python file",
            )

        try:
            return _dispatch(args, root, file_path)
        except SyntaxError as exc:
            return tool_error(
                f"Could not parse {file_path.name}: {exc}",
                root_cause=f"syntax error at line {exc.lineno}",
                safe_retry="fix the syntax error, then retry",
                stop_condition="do not retry until the file parses",
            )


def _dispatch(args: LspInput, root: Path, file_path: Path) -> ToolResult:
    if args.operation == "document_symbol":
        results = list_document_symbols(file_path)
        return ToolResult(
            content=_format_symbols(results, root),
            metadata={"result_count": len(results), "summary": f"{len(results)} symbol(s)"},
        )
    if args.operation == "go_to_definition":
        results = go_to_definition(
            root=root, file_path=file_path, symbol=args.symbol, line=args.line, character=args.character
        )
        return ToolResult(
            content=_format_symbols(results, root),
            metadata={"result_count": len(results), "summary": f"{len(results)} definition(s)"},
        )
    if args.operation == "find_references":
        refs = find_references(
            root=root, file_path=file_path, symbol=args.symbol, line=args.line, character=args.character
        )
        return ToolResult(
            content=_format_references(refs, root),
            metadata={"result_count": len(refs), "summary": f"{len(refs)} reference(s)"},
        )
    result = hover(
        root=root, file_path=file_path, symbol=args.symbol, line=args.line, character=args.character
    )
    if result is None:
        return ToolResult(content="(no hover result)", metadata={"result_count": 0})
    parts = [
        f"{result.kind} {result.name}",
        f"path: {_display(result.path, root)}:{result.line}:{result.character}",
    ]
    if result.signature:
        parts.append(f"signature: {result.signature}")
    if result.docstring:
        parts.append(f"docstring: {result.docstring.strip()}")
    return ToolResult(content="\n".join(parts), metadata={"result_count": 1})


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _format_symbols(results: list[SymbolLocation], root: Path) -> str:
    if not results:
        return "(no results)"
    lines: list[str] = []
    for item in results:
        lines.append(f"{item.kind} {item.name} - {_display(item.path, root)}:{item.line}:{item.character}")
        if item.signature:
            lines.append(f"  signature: {item.signature}")
        if item.docstring:
            lines.append(f"  docstring: {item.docstring.strip().splitlines()[0]}")
    return "\n".join(lines)


def _format_references(results: list[tuple[Path, int, str]], root: Path) -> str:
    if not results:
        return "(no results)"
    return "\n".join(f"{_display(path, root)}:{line}:{text}" for path, line, text in results)


__all__ = ["LspInput", "LspTool"]
