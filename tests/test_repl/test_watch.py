"""Unit tests for dream.repl._watch — JSONL event file tail + formatting.

Covers _colour_for(), _format(), and run_watch() with a simulated file.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from dream.repl._watch import _colour_for, _format, run_watch

# --- _colour_for (lines 24-60) ---


def test_colour_for_substrate_failover() -> None:
    assert _colour_for("substrate.failover") == "\x1b[35m"
    assert _colour_for("substrate.failover.started") == "\x1b[35m"


def test_colour_for_substrate_health_recovered() -> None:
    assert _colour_for("substrate.health.recovered") == "\x1b[32m"


def test_colour_for_substrate_health_degraded() -> None:
    assert _colour_for("substrate.health.degraded") == "\x1b[33m"


def test_colour_for_turn_completed() -> None:
    assert _colour_for("turn.completed") == "\x1b[32m"


def test_colour_for_turn_attempt_failed() -> None:
    assert _colour_for("turn.attempt_failed") == "\x1b[31m"


def test_colour_for_context_compaction_completed() -> None:
    assert _colour_for("context.compaction.completed") == "\x1b[36m"


def test_colour_for_context_compaction_triggered() -> None:
    assert _colour_for("context.compaction.triggered") == "\x1b[33m"


def test_colour_for_heartbeat_decision_run() -> None:
    assert _colour_for("heartbeat.decision.run") == "\x1b[32m"


def test_colour_for_heartbeat_decision_forced() -> None:
    assert _colour_for("heartbeat.decision.forced") == "\x1b[33m"


def test_colour_for_heartbeat_decision_skip() -> None:
    assert _colour_for("heartbeat.decision.skip") == "\x1b[2m"


def test_colour_for_heartbeat_missing() -> None:
    assert _colour_for("heartbeat.missing") == "\x1b[31m"


def test_colour_for_wake_dropped() -> None:
    assert _colour_for("wake.dropped") == "\x1b[2m"


def test_colour_for_session_error() -> None:
    assert _colour_for("session.error") == "\x1b[31m"


def test_colour_for_session_turn_failed() -> None:
    assert _colour_for("session.turn_failed") == "\x1b[31m"


def test_colour_for_session_turn_complete() -> None:
    assert _colour_for("session.turn_complete") == "\x1b[32m"


def test_colour_for_session_repl_prefix() -> None:
    assert _colour_for("session.repl.foo") == "\x1b[36m"


def test_colour_for_session_generic() -> None:
    assert _colour_for("session.started") == "\x1b[2m"


def test_colour_for_repl_prefix() -> None:
    assert _colour_for("repl.command") == "\x1b[36m"


def test_colour_for_unknown() -> None:
    assert _colour_for("some.unknown.event") == ""


# --- _format (lines 63-71) ---


def test_format_with_colour() -> None:
    record: dict[str, object] = {"ts": "2025-01-01T00:00:00", "type": "turn.completed", "extra": "val"}
    result = _format(record, use_colour=True)
    assert "2025-01-01T00:00:00" in result
    assert "turn.completed" in result
    assert "extra=val" in result
    assert "\x1b[" in result  # ANSI codes present


def test_format_without_colour() -> None:
    record: dict[str, object] = {"ts": "2025-01-01T00:00:00", "type": "turn.completed", "extra": "val"}
    result = _format(record, use_colour=False)
    assert result == "2025-01-01T00:00:00 turn.completed extra=val"
    assert "\x1b[" not in result


def test_format_strips_pid() -> None:
    record: dict[str, object] = {"ts": "t", "type": "x", "pid": 12345, "key": "v"}
    result = _format(record, use_colour=False)
    assert "pid" not in result
    assert "key=v" in result


def test_format_missing_ts_and_type() -> None:
    record: dict[str, object] = {"key": "v"}
    result = _format(record, use_colour=False)
    assert "unknown" in result  # default type
    assert "key=v" in result


def test_format_empty_payload() -> None:
    record: dict[str, object] = {"ts": "t", "type": "x"}
    result = _format(record, use_colour=False)
    assert result == "t x"


# --- run_watch (lines 74-106) ---


def test_run_watch_reads_from_start(tmp_path: Path, capsys: object) -> None:
    """run_watch with from_start=True reads existing lines."""
    event_file = tmp_path / "events.jsonl"
    records = [
        {"ts": "t1", "type": "turn.completed"},
        {"ts": "t2", "type": "session.error", "detail": "oops"},
    ]
    event_file.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )

    # run_watch blocks in a while-True loop, so run it in a thread and
    # kill it after it processes existing lines.
    stop = threading.Event()

    def _run() -> None:
        import builtins

        lines_printed: list[str] = []
        original_print = builtins.print

        def capturing_print(*args: object, **kwargs: object) -> None:
            lines_printed.append(str(args[0]) if args else "")
            if len(lines_printed) >= 2:
                stop.set()

        builtins.print = capturing_print
        try:
            # We can't easily break the while True loop, so we rely on
            # the thread being daemonic and the test finishing.
            run_watch(event_file, from_start=True, use_colour=False)
        except Exception:
            pass
        finally:
            builtins.print = original_print

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    stop.wait(timeout=5)
    assert stop.is_set()


def test_run_watch_skips_malformed_lines(tmp_path: Path) -> None:
    """Malformed JSON lines are printed with a [malformed line] prefix."""
    event_file = tmp_path / "events.jsonl"
    event_file.write_text(
        "not json\n" + json.dumps({"ts": "t", "type": "ok"}) + "\n",
        encoding="utf-8",
    )

    printed: list[str] = []
    stop = threading.Event()

    def _run() -> None:
        import builtins

        original_print = builtins.print

        def capturing_print(*args: object, **kwargs: object) -> None:
            printed.append(str(args[0]) if args else "")
            if len(printed) >= 2:
                stop.set()

        builtins.print = capturing_print
        try:
            run_watch(event_file, from_start=True, use_colour=False)
        except Exception:
            pass
        finally:
            builtins.print = original_print

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    stop.wait(timeout=5)
    assert any("[malformed line]" in p for p in printed)


def test_run_watch_skips_blank_lines(tmp_path: Path) -> None:
    """Empty lines between records are silently skipped."""
    event_file = tmp_path / "events.jsonl"
    event_file.write_text(
        "\n\n" + json.dumps({"ts": "t", "type": "ok"}) + "\n",
        encoding="utf-8",
    )

    printed: list[str] = []
    stop = threading.Event()

    def _run() -> None:
        import builtins

        original_print = builtins.print

        def capturing_print(*args: object, **kwargs: object) -> None:
            printed.append(str(args[0]) if args else "")
            if len(printed) >= 1:
                stop.set()

        builtins.print = capturing_print
        try:
            run_watch(event_file, from_start=True, use_colour=False)
        except Exception:
            pass
        finally:
            builtins.print = original_print

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    stop.wait(timeout=5)
    assert stop.is_set()
    # Only the valid record should be printed, no blank-line artifacts.
    assert all("[malformed" not in p for p in printed)


def test_run_watch_waits_for_file(tmp_path: Path) -> None:
    """run_watch prints a waiting message when the file doesn't exist yet."""
    event_file = tmp_path / "events.jsonl"

    printed: list[str] = []
    stop = threading.Event()

    def _run() -> None:
        import builtins

        original_print = builtins.print

        def capturing_print(*args: object, **kwargs: object) -> None:
            printed.append(str(args[0]) if args else "")
            if any("waiting" in p for p in printed):
                # Create the file to unblock the wait loop.
                event_file.write_text(
                    json.dumps({"ts": "t", "type": "ok"}) + "\n", encoding="utf-8"
                )
                stop.set()

        builtins.print = capturing_print
        try:
            run_watch(event_file, from_start=True, use_colour=False)
        except Exception:
            pass
        finally:
            builtins.print = original_print

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    stop.wait(timeout=10)
    assert any("waiting" in p for p in printed)
