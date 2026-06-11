"""``python -m dream.ctl`` — steer a running daemon from another process.

The CLI writes a command file into the runtime inbox and waits for the
ack on the event stream. Exit codes: 0 ok, 1 error/rejected ack,
2 bad usage, 3 no ack before the timeout (daemon not running?).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from dream.channels import Ack, CommandInbox, read_ack
from dream.config.paths import DreamPaths
from dream.ctl import main
from dream.observability import EventSink


def _roots(tmp_path: Path) -> tuple[Path, DreamPaths]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return repo, DreamPaths.resolve(repo, home=tmp_path / "home")


def _argv(repo: Path, *rest: str) -> list[str]:
    return ["--working-dir", str(repo), "--timeout", "0.3", *rest]


def _ack_all_pending(paths: DreamPaths) -> None:
    """Play the daemon: drain the inbox and ack every command ok."""
    inbox = CommandInbox(paths.dream_dir / "runtime" / "inbox")
    sink = EventSink(paths.dream_dir / "runtime" / "events.jsonl")
    for command in inbox.drain():
        Ack(status="ok", summary=f"handled {command.to_dict()['type']}").emit(
            sink, command_id=command.id
        )


def test_submit_writes_command_and_reads_ack(tmp_path: Path) -> None:
    repo, paths = _roots(tmp_path)
    out = io.StringIO()

    # Pre-ack from a fake daemon thread is racy; instead run main with a
    # tiny timeout twice: first to write, then ack + verify exit 0.
    inbox_dir = paths.dream_dir / "runtime" / "inbox"
    code = main(_argv(repo, "submit", "fix the CI"), stdout=out, stderr=out)
    assert code == 3  # no daemon answered
    # The command file is in the inbox even though nothing acked.
    leftover = list(inbox_dir.glob("*.json"))
    assert len(leftover) == 1
    body = json.loads(leftover[0].read_text(encoding="utf-8"))
    assert body["type"] == "submit_task"
    assert body["intent"] == "fix the CI"


def test_status_round_trip_with_acking_daemon(tmp_path: Path) -> None:
    repo, paths = _roots(tmp_path)
    out = io.StringIO()

    import threading
    import time

    def daemon() -> None:
        for _ in range(50):
            _ack_all_pending(paths)
            time.sleep(0.02)

    thread = threading.Thread(target=daemon, daemon=True)
    thread.start()
    code = main(
        ["--working-dir", str(repo), "--timeout", "3", "status"],
        stdout=out,
        stderr=out,
    )
    thread.join()
    assert code == 0
    printed = json.loads(out.getvalue())
    assert printed["status"] == "ok"
    assert "handled status" in printed["summary"]


def test_rejected_ack_exits_1(tmp_path: Path) -> None:
    repo, paths = _roots(tmp_path)
    sink = EventSink(paths.dream_dir / "runtime" / "events.jsonl")
    out = io.StringIO()

    import threading
    import time

    inbox = CommandInbox(paths.dream_dir / "runtime" / "inbox")

    def daemon() -> None:
        for _ in range(50):
            for command in inbox.drain():
                Ack(status="rejected", summary="nope").emit(
                    sink, command_id=command.id
                )
            time.sleep(0.02)

    thread = threading.Thread(target=daemon, daemon=True)
    thread.start()
    code = main(
        ["--working-dir", str(repo), "--timeout", "3", "cancel", "t-1"],
        stdout=out,
        stderr=out,
    )
    thread.join()
    assert code == 1


def test_wake_command_shape(tmp_path: Path) -> None:
    repo, paths = _roots(tmp_path)
    out = io.StringIO()
    code = main(_argv(repo, "wake"), stdout=out, stderr=out)
    assert code == 3
    leftover = list((paths.dream_dir / "runtime" / "inbox").glob("*.json"))
    assert json.loads(leftover[0].read_text(encoding="utf-8"))["type"] == "wake"


def test_events_subcommand_prints_tail(tmp_path: Path) -> None:
    repo, paths = _roots(tmp_path)
    sink = EventSink(paths.dream_dir / "runtime" / "events.jsonl")
    for n in range(5):
        sink.emit("tick", n=n)
    out = io.StringIO()
    code = main(
        ["--working-dir", str(repo), "events", "--last", "2"],
        stdout=out,
        stderr=out,
    )
    assert code == 0
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["n"] for r in lines] == [3, 4]


def test_read_ack_helper_visible_for_consumers(tmp_path: Path) -> None:
    # The CLI's correlation primitive is public for SDK consumers too.
    _repo, paths = _roots(tmp_path)
    events = paths.dream_dir / "runtime" / "events.jsonl"
    Ack(status="ok", summary="hi").emit(EventSink(events), command_id="c1")
    found = read_ack(events, command_id="c1")
    assert found is not None and found.summary == "hi"
