"""``python -m dream.repl`` — subcommands ``chat``, ``session``, and ``watch``."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dream.repl._chat import build_specs, run_chat
from dream.repl._session import run_session_repl
from dream.repl._watch import run_watch

_DEFAULT_EVENTS = Path(".dream") / "repl-events.jsonl"
_DEFAULT_ENV_FILE = Path(".env.local")


def _positive_int(raw: str) -> int:
    """argparse type: a strictly positive integer.

    Guards ``--max-turns`` so a 0/negative value fails fast at parse time
    instead of the engine silently doing zero turns and returning nothing.
    """
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return value


def _load_env_file(path: Path) -> int:
    """Load ``KEY=VALUE`` lines from ``path`` into ``os.environ`` (no overwrite).

    Tiny ad-hoc parser — no quoting, no escapes, no ``export`` prefix. Just
    enough for the dev REPL. Lines starting with ``#`` or blank are skipped.
    Returns the number of keys set; missing file returns 0.
    """
    if not path.is_file():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip exactly one matched surrounding quote pair, so an inner quote
        # (e.g. "'key'") is preserved rather than chained-stripped away.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dream.repl",
        description=(
            "Dev REPL for Spec 02 substrates. "
            "Reads DREAM_SMOKE_API_KEY / _BASE_URL / _MODEL from the environment."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="interactive prompt loop")
    chat.add_argument(
        "--events",
        type=Path,
        default=_DEFAULT_EVENTS,
        help=f"JSONL event file (default: {_DEFAULT_EVENTS})",
    )
    chat.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="default max_tokens per turn (default: 1024)",
    )
    chat.add_argument(
        "--no-stream",
        action="store_true",
        help="start with streaming off (toggle with /stream on)",
    )
    chat.add_argument(
        "--fake-primary",
        action="store_true",
        help="insert a fake-failing substrate at order[0] to demo immediate failover",
    )
    chat.add_argument(
        "--fake-fallback",
        action="store_true",
        help="append a fake-failing substrate as the last fallback",
    )

    watch = sub.add_parser("watch", help="tail the JSONL event file")
    watch.add_argument(
        "--events",
        type=Path,
        default=_DEFAULT_EVENTS,
        help=f"JSONL event file (default: {_DEFAULT_EVENTS})",
    )
    watch.add_argument(
        "--from-start",
        action="store_true",
        help="read from the file's beginning instead of tailing the new tail",
    )
    watch.add_argument(
        "--no-colour",
        action="store_true",
        help="disable ANSI colour even if stdout is a TTY",
    )

    session = sub.add_parser(
        "session",
        help="interactive Spec 05 Session loop against a real provider",
    )
    session.add_argument(
        "--events",
        type=Path,
        default=_DEFAULT_EVENTS,
        help=f"JSONL event file (default: {_DEFAULT_EVENTS})",
    )
    session.add_argument(
        "--model",
        default=None,
        help="override DREAM_SMOKE_MODEL for this session",
    )
    session.add_argument(
        "--system",
        default=None,
        help="system prompt prepended to every turn",
    )
    session.add_argument(
        "--max-turns",
        type=_positive_int,
        default=8,
        help="hard cap on assistant turns per send; must be >= 1 (default: 8)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env_file(_DEFAULT_ENV_FILE)
    args = _build_parser().parse_args(argv)
    if args.command == "chat":
        specs = build_specs(
            fake_primary=args.fake_primary,
            fake_fallback=args.fake_fallback,
        )
        return run_chat(
            specs,
            events_path=args.events,
            max_tokens=args.max_tokens,
            initial_stream=not args.no_stream,
        )
    if args.command == "watch":
        return run_watch(
            args.events,
            from_start=args.from_start,
            use_colour=False if args.no_colour else None,
        )
    if args.command == "session":
        return run_session_repl(
            events_path=args.events,
            model=args.model,
            system=args.system,
            max_turns=args.max_turns,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
