"""``python -m dream.ctl`` — steer a running daemon from outside (spec 15 P2 §4).

Writes a typed command file into the runtime inbox
(``.dream/runtime/inbox/``) and waits for the matching
``runtime.command.ack`` on the event stream. Humans and chorus's
employees use the same door; a socket/HTTP gateway would be a later
adapter behind the same command types.

Subcommands::

    dream.ctl submit "fix the CI" [--task-id t-x] [--max-sprints 3]
    dream.ctl status
    dream.ctl cancel <task-id>
    dream.ctl wake
    dream.ctl events [--last N]

Exit codes: 0 ok ack, 1 error/rejected ack, 2 bad usage, 3 ack timeout
(is the daemon running?).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from dream.channels import (
    CancelCommand,
    Command,
    CommandInbox,
    StatusCommand,
    SubmitTaskCommand,
    WakeCommand,
    wait_for_ack,
)
from dream.config.paths import DreamPaths
from dream.observability import tail_events
from dream.utils.fs import compact_json

__all__ = ["main", "parse_args"]

EXIT_OK = 0
EXIT_NOT_OK_ACK = 1
EXIT_USAGE = 2
EXIT_NO_ACK = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m dream.ctl",
        description="Send a command to a running dream daemon.",
    )
    parser.add_argument("--working-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="seconds to wait for the ack (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    submit = sub.add_parser("submit", help="start an end-to-end task")
    submit.add_argument("intent")
    submit.add_argument("--task-id", default=None)
    submit.add_argument("--max-sprints", type=int, default=None)

    cancel = sub.add_parser("cancel", help="cancel a job or background task")
    cancel.add_argument("task_id")

    sub.add_parser("status", help="ask the runtime what it is doing")
    sub.add_parser("wake", help="fire one wake cycle now")

    events = sub.add_parser("events", help="print the runtime event stream")
    events.add_argument("--last", type=int, default=None)
    return parser.parse_args(argv)


def _build_command(args: argparse.Namespace) -> Command:
    if args.subcommand == "submit":
        return SubmitTaskCommand(
            intent=args.intent, task_id=args.task_id, max_sprints=args.max_sprints
        )
    if args.subcommand == "cancel":
        return CancelCommand(task_id=args.task_id)
    if args.subcommand == "status":
        return StatusCommand()
    return WakeCommand()


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on usage errors; normalise to our contract.
        return EXIT_USAGE if exc.code else EXIT_OK
    paths = DreamPaths.resolve(args.working_dir)
    runtime_dir = paths.dream_dir / "runtime"
    events_path = runtime_dir / "events.jsonl"

    if args.subcommand == "events":
        for record in tail_events(events_path, last=args.last):
            out.write(compact_json(record) + "\n")
        return EXIT_OK

    try:
        command = _build_command(args)
    except ValueError as exc:
        err.write(f"invalid command: {exc}\n")
        return EXIT_USAGE
    CommandInbox(runtime_dir / "inbox").submit(command)
    ack = wait_for_ack(
        events_path, command_id=command.id, timeout_seconds=args.timeout
    )
    if ack is None:
        err.write(
            "no ack received — is the daemon running? "
            f"(inbox: {runtime_dir / 'inbox'})\n"
        )
        return EXIT_NO_ACK
    out.write(
        json.dumps(
            {
                "status": ack.status,
                "summary": ack.summary,
                "next_actions": list(ack.next_actions),
                "artifacts": list(ack.artifacts),
            },
            indent=2,
        )
        + "\n"
    )
    return EXIT_OK if ack.status == "ok" else EXIT_NOT_OK_ACK
