"""Spec 10 cross-cutting lints for ``src/dream/swarm/``.

Two rules with teeth:

1. The swarm package must never write under ``~`` or call ``expanduser``.
   The whole point of the dream divergence from OpenHarness is that swarm
   state lives **in the worktree** so it is inspectable + committable.

2. The swarm package must never reach into another agent's in-process state
   to deliver a message. The mailbox files ARE the bus. We approximate this
   with two checks: no ``queue``/``asyncio.Queue`` imports in modules other
   than mailbox internals, and no in-module references to a sibling agent's
   send/receive method (e.g. ``teammate.send(``, ``other.receive(``) — these
   would short-circuit the file bus.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC_SWARM = Path(__file__).resolve().parent.parent.parent / "src" / "dream" / "swarm"


def _py_files() -> list[Path]:
    return sorted(p for p in SRC_SWARM.rglob("*.py") if "__pycache__" not in p.parts)


# --- rule 1: no home-dir paths in swarm/ ---------------------------------


_HOME_DIR_FORBIDDEN = re.compile(
    r"""
    expanduser            # os.path.expanduser / Path.expanduser
    | Path\.home\(\)
    | ~/\.openharness     # OpenHarness's path scheme — must not appear at all
    | os\.environ\[       # reading HOME / USERPROFILE for path construction
        ['"](HOME|USERPROFILE)['"]
    \]
    """,
    re.VERBOSE,
)


def test_swarm_never_references_home_directory() -> None:
    violators: list[tuple[Path, str]] = []
    for f in _py_files():
        source = f.read_text(encoding="utf-8")
        m = _HOME_DIR_FORBIDDEN.search(source)
        if m:
            violators.append((f, m.group(0)))
    assert not violators, (
        "src/dream/swarm/ must use in-worktree paths (.harness/swarm/...), not "
        "home-directory paths. Spec 10 divergence from OpenHarness. Hits:\n"
        + "\n".join(f"  {f}: {hit!r}" for f, hit in violators)
    )


# --- rule 2: no in-process messaging primitives between agents -----------


def _imports_in(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                out.add(f"{mod}.{alias.name}".lstrip("."))
    return out


def test_swarm_does_not_use_in_memory_queues_for_messaging() -> None:
    # ``queue.Queue`` and ``asyncio.Queue`` are exactly the patterns that
    # would let a producer hand a message to a consumer without round-
    # tripping through the file bus. Nothing in swarm/ should reach for
    # them.  ``in_process.py`` is the in-process *executor* (a coroutine
    # scheduler), not an inter-agent queue — but it must STILL use the
    # mailbox API for any inter-agent message, so the rule is universal.
    forbidden = {"queue", "queue.Queue", "asyncio.Queue"}
    violators: list[tuple[Path, str]] = []
    for f in _py_files():
        imports = _imports_in(f)
        for bad in forbidden:
            if bad in imports:
                violators.append((f, bad))
    assert not violators, (
        "src/dream/swarm/ must use the mailbox file bus for inter-agent "
        "messaging, never in-process queues. Hits:\n"
        + "\n".join(f"  {f}: {bad}" for f, bad in violators)
    )


_DIRECT_DELIVERY_RE = re.compile(
    r"""
    (?:teammate|sibling|other_agent|peer)\.(send|receive|deliver|notify)\(
    """,
    re.VERBOSE,
)


def test_swarm_does_not_call_sibling_agent_methods_directly() -> None:
    violators: list[tuple[Path, str]] = []
    for f in _py_files():
        source = f.read_text(encoding="utf-8")
        for m in _DIRECT_DELIVERY_RE.finditer(source):
            violators.append((f, m.group(0)))
    assert not violators, (
        "src/dream/swarm/ must not short-circuit the mailbox bus by calling "
        "a sibling agent's send/receive method directly. Use the file mailbox "
        "instead. Hits:\n"
        + "\n".join(f"  {f}: {hit!r}" for f, hit in violators)
    )
