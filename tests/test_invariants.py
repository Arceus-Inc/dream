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


def _files_calling_print(*, exempt: set[Path]) -> list[Path]:
    hits: list[Path] = []
    for f in _py_files():
        if f in exempt:
            continue
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
    """Spec 00 rule 4: no ``print()`` in the SDK.

    The ``dream.repl`` package is an interactive developer CLI, not part of
    the SDK consumed by client code — its whole job is to render to stdout.
    Exempt the subtree (and only the subtree) rather than rerouting every
    print through a wrapper for show.
    """
    repl_dir = SRC / "repl"
    exempt = {p for p in _py_files() if repl_dir in p.parents or p == repl_dir}
    violators = _files_calling_print(exempt=exempt)
    assert not violators, f"print() called in src: {violators}"


_WRITE_MODE_CHARS = ("w", "a", "x", "+")


def _string_assignments(tree: ast.Module) -> dict[str, set[str]]:
    """Map variable names to the string-constant values assigned to them.

    A conservative approximation that lets the direct-write lint see through
    ``mode = "w"; open(path, mode)``. Reassignment unions the values, so a name
    that is ever assigned a write-capable mode is treated as write-capable.
    """
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out.setdefault(target.id, set()).add(value.value)
    return out


def _open_mode_values(mode_arg: ast.expr, assignments: dict[str, set[str]]) -> set[str] | None:
    """Resolve an ``open()`` mode argument to its possible string values.

    Returns ``None`` when the mode is computed/unresolvable (e.g. a function
    call), which the caller treats as "flag for manual review" rather than
    silently passing.
    """
    if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
        return {mode_arg.value}
    if isinstance(mode_arg, ast.Name) and mode_arg.id in assignments:
        return assignments[mode_arg.id]
    return None


def _scan_source(source: str) -> list[tuple[int, str]]:
    """Direct-write findings (lineno, description) for a single source string."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    assignments = _string_assignments(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"write_text", "write_bytes"}:
                hits.append((node.lineno, f".{node.func.attr}(...)"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
        ):
            mode_arg: ast.expr | None = node.args[1] if len(node.args) >= 2 else None
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode_arg = kw.value
            if mode_arg is None:
                continue  # default mode is "r" (read-only)
            values = _open_mode_values(mode_arg, assignments)
            if values is None:
                # Non-literal / computed mode: cannot prove read-only — flag it.
                hits.append((node.lineno, "open(..., <non-literal mode>) — manual review"))
            elif any(c in v for v in values for c in _WRITE_MODE_CHARS):
                hits.append((node.lineno, f"open(..., mode in {sorted(values)})"))
    return hits


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
        for lineno, desc in _scan_source(f.read_text(encoding="utf-8")):
            hits.append((f, lineno, desc))
    return hits


def test_writes_route_through_atomic_helper() -> None:
    """Spec 01 decision 9: every harness-initiated write is atomic."""
    hits = _direct_writes_in_src()
    assert not hits, (
        "Direct writes outside utils/fs.atomic_write_*. Route through the helper:\n"
        + "\n".join(f"  {f}:{lineno}: {call}" for f, lineno, call in hits)
    )


def test_lint_catches_literal_write_open() -> None:
    assert _scan_source('open("p", "w")')


def test_lint_catches_computed_write_open() -> None:
    # The bug the reviewer flagged: a write mode behind a variable.
    assert _scan_source('mode = "w"\nopen("p", mode)')


def test_lint_flags_non_literal_open_mode_for_review() -> None:
    # An unresolvable (computed) mode must be flagged, not silently allowed.
    assert _scan_source('open("p", choose_mode())')


def test_lint_allows_resolved_read_open() -> None:
    assert not _scan_source('mode = "r"\nopen("p", mode)')


def test_lint_allows_default_read_open() -> None:
    assert not _scan_source('open("p")')
    assert not _scan_source('open("p", "rb")')


def test_lint_catches_write_text_attribute() -> None:
    assert _scan_source('p.write_text("x")')
