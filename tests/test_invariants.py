"""Spec 00 cross-cutting invariants, applied to the Spec 01 modules that exist.

These are architectural lints, not behavioural tests. They guard decisions that
span multiple modules and would otherwise erode silently as the codebase grows:

- subprocess usage stays in one auditable wrapper (Spec 01: ``utils/git.py``).
- POSIX/Windows locking primitives stay behind one context manager
  (Spec 01 decision 10).
- every harness-initiated write routes through the atomic-write helper
  (Spec 01 decision 9; Spec 00 invariant 2: "the repo is the system of record"
  is only *safe* under crashes if writes are atomic).
- the SDK emits typed events, never ``print()`` or ``logging`` side effects
  (Spec 00 design rule 4).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "dream"


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _files_importing(module: str, *, exempt: set[Path]) -> list[Path]:
    """Return files under ``src/dream/`` that import ``module`` (top-level or from)."""
    hits: list[Path] = []
    for f in _py_files():
        if f in exempt:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    alias.name == module or alias.name.startswith(module + ".")
                    for alias in node.names
                ):
                    hits.append(f)
                    break
            elif isinstance(node, ast.ImportFrom):
                src = node.module or ""
                if src == module or src.startswith(module + "."):
                    hits.append(f)
                    break
    return hits


def test_subprocess_only_in_utils_git() -> None:
    """Spec 01: every git/CLI call goes through the single ``run_git`` wrapper."""
    allowed = {SRC / "utils" / "git.py"}
    violators = _files_importing("subprocess", exempt=allowed)
    assert not violators, f"subprocess imported outside utils/git.py: {violators}"


def test_fcntl_only_in_utils_file_lock() -> None:
    """Spec 01 decision 10: POSIX locking primitive hidden behind one helper."""
    allowed = {SRC / "utils" / "file_lock.py"}
    violators = _files_importing("fcntl", exempt=allowed)
    assert not violators, f"fcntl imported outside utils/file_lock.py: {violators}"


def test_msvcrt_only_in_utils_file_lock() -> None:
    """Spec 01 decision 10: Windows locking primitive hidden behind one helper."""
    allowed = {SRC / "utils" / "file_lock.py"}
    violators = _files_importing("msvcrt", exempt=allowed)
    assert not violators, f"msvcrt imported outside utils/file_lock.py: {violators}"


def test_no_logging_in_src() -> None:
    """Spec 00 rule 4: consumers see typed Events, never logging side effects."""
    violators = _files_importing("logging", exempt=set())
    assert not violators, f"logging imported in src: {violators}"


def _files_calling_print() -> list[Path]:
    hits: list[Path] = []
    for f in _py_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                hits.append(f)
                break
    return hits


def test_no_print_calls_in_src() -> None:
    """Spec 00 rule 4: no ``print()`` in the SDK."""
    violators = _files_calling_print()
    assert not violators, f"print() called in src: {violators}"


def _direct_writes_in_src() -> list[tuple[Path, int, str]]:
    """Find Path.write_text/.write_bytes and write-capable open() outside fs.py.

    ``file_lock.py`` is exempt because it opens the lockfile for read+write to
    hold the OS lock — there is no payload to atomically swap there.
    """
    allowed = {
        SRC / "utils" / "fs.py",
        SRC / "utils" / "file_lock.py",
    }
    hits: list[tuple[Path, int, str]] = []
    for f in _py_files():
        if f in allowed:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"write_text", "write_bytes"}:
                    hits.append((f, node.lineno, f".{node.func.attr}(...)"))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"
            ):
                mode_arg: ast.expr | None = None
                if len(node.args) >= 2:
                    mode_arg = node.args[1]
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode_arg = kw.value
                if (
                    isinstance(mode_arg, ast.Constant)
                    and isinstance(mode_arg.value, str)
                    and any(c in mode_arg.value for c in ("w", "a", "x", "+"))
                ):
                    hits.append((f, node.lineno, f"open(..., {mode_arg.value!r})"))
    return hits


def test_writes_route_through_atomic_helper() -> None:
    """Spec 01 decision 9: every harness-initiated write is atomic."""
    hits = _direct_writes_in_src()
    assert not hits, (
        "Direct writes outside utils/fs.atomic_write_*. Route through the helper:\n"
        + "\n".join(f"  {f}:{lineno}: {call}" for f, lineno, call in hits)
    )
