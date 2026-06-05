"""``python -m dream.repl`` — subcommands ``chat`` and ``watch``."""

from __future__ import annotations

import argparse
from pathlib import Path

from dream.repl._chat import build_specs, run_chat
from dream.repl._watch import run_watch

_DEFAULT_EVENTS = Path(".dream") / "repl-events.jsonl"


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

    return parser


def main(argv: list[str] | None = None) -> int:
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
