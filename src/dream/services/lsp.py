"""Lightweight code-intelligence helpers for the ``lsp`` tool.

Ported from OpenHarness ``services/lsp``. Intentionally smaller than a real
language-server integration: it walks Python source with :mod:`ast` and a few
regexes to provide stable, read-only definition / reference / hover / symbol
queries — no language-server process, no JSON-RPC. Python-only by design.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

_PYTHON_GLOB = "*.py"
_SKIP_PARTS = {".git", ".hg", ".svn", ".venv", "venv", "__pycache__", "node_modules"}


@dataclass(frozen=True)
class SymbolLocation:
    """Resolved symbol location inside the workspace."""

    name: str
    kind: str
    path: Path
    line: int
    character: int
    signature: str = ""
    docstring: str = ""


def list_document_symbols(path: Path) -> list[SymbolLocation]:
    """Return top-level and nested symbols from one Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols: list[SymbolLocation] = []
    _collect_symbols(tree, path, symbols, parent=None)
    return symbols


def workspace_symbol_search(root: Path, query: str) -> list[SymbolLocation]:
    """Return symbols whose name contains ``query`` (case-insensitive)."""
    needle = query.lower().strip()
    if not needle:
        return []
    matches: list[SymbolLocation] = []
    for file_path in iter_python_files(root):
        try:
            symbols = list_document_symbols(file_path)
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        for symbol in symbols:
            if needle in symbol.name.lower():
                matches.append(symbol)
    return matches


def go_to_definition(
    *,
    root: Path,
    file_path: Path,
    symbol: str | None = None,
    line: int | None = None,
    character: int | None = None,
) -> list[SymbolLocation]:
    """Resolve candidate definitions for a symbol by name across the workspace."""
    target = symbol or extract_symbol_at_position(file_path, line=line, character=character)
    if not target:
        return []
    matches: list[SymbolLocation] = []
    for candidate in iter_python_files(root):
        try:
            symbols = list_document_symbols(candidate)
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        matches.extend(item for item in symbols if item.name.split(".")[-1] == target)
    return matches


def find_references(
    *,
    root: Path,
    file_path: Path,
    symbol: str | None = None,
    line: int | None = None,
    character: int | None = None,
) -> list[tuple[Path, int, str]]:
    """Return line-oriented references (whole-word) for a symbol."""
    target = symbol or extract_symbol_at_position(file_path, line=line, character=character)
    if not target:
        return []
    pattern = re.compile(rf"\b{re.escape(target)}\b")
    matches: list[tuple[Path, int, str]] = []
    for candidate in iter_python_files(root):
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            if pattern.search(raw_line):
                matches.append((candidate, lineno, raw_line.strip()))
    return matches


def hover(
    *,
    root: Path,
    file_path: Path,
    symbol: str | None = None,
    line: int | None = None,
    character: int | None = None,
) -> SymbolLocation | None:
    """Return the best hover target (first matching definition) for a symbol."""
    matches = go_to_definition(
        root=root, file_path=file_path, symbol=symbol, line=line, character=character
    )
    return matches[0] if matches else None


def extract_symbol_at_position(
    file_path: Path, *, line: int | None, character: int | None
) -> str | None:
    """Extract a probable identifier from a 1-based line/character position."""
    if line is None:
        return None
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if line < 1 or line > len(lines):
        return None
    text = lines[line - 1]
    if not text:
        return None
    index = max(0, min((character or 1) - 1, len(text) - 1))
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", text):
        if match.start() <= index < match.end():
            return match.group(0)
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", text):
        return match.group(0)
    return None


def iter_python_files(root: Path) -> list[Path]:
    """Return Python source files under ``root`` in a stable order."""
    files: list[Path] = []
    for path in root.rglob(_PYTHON_GLOB):
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    files.sort()
    return files


def _collect_symbols(
    node: ast.AST, path: Path, bucket: list[SymbolLocation], *, parent: str | None
) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{parent}.{child.name}" if parent else child.name
            args = [arg.arg for arg in child.args.args]
            bucket.append(
                SymbolLocation(
                    name=name,
                    kind="function",
                    path=path,
                    line=child.lineno,
                    character=child.col_offset + 1,
                    signature=f"def {child.name}({', '.join(args)})",
                    docstring=ast.get_docstring(child) or "",
                )
            )
            _collect_symbols(child, path, bucket, parent=name)
        elif isinstance(child, ast.ClassDef):
            name = f"{parent}.{child.name}" if parent else child.name
            bucket.append(
                SymbolLocation(
                    name=name,
                    kind="class",
                    path=path,
                    line=child.lineno,
                    character=child.col_offset + 1,
                    signature=f"class {child.name}",
                    docstring=ast.get_docstring(child) or "",
                )
            )
            _collect_symbols(child, path, bucket, parent=name)
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    name = f"{parent}.{target.id}" if parent else target.id
                    bucket.append(
                        SymbolLocation(
                            name=name,
                            kind="variable",
                            path=path,
                            line=target.lineno,
                            character=target.col_offset + 1,
                            signature=f"{target.id} = ...",
                        )
                    )
        else:
            _collect_symbols(child, path, bucket, parent=parent)


__all__ = [
    "SymbolLocation",
    "extract_symbol_at_position",
    "find_references",
    "go_to_definition",
    "hover",
    "iter_python_files",
    "list_document_symbols",
    "workspace_symbol_search",
]
